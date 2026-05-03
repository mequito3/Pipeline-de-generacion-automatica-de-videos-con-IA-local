import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_TOKEN_FILE = Path(__file__).parent.parent / "youtube_publisher_token.json"
_SCOPES = ["https://www.googleapis.com/auth/youtube"]
_REPLIED_FILE = Path(__file__).parent.parent / "output" / "replied_comments.json"

_SPAM_WORDS = [
    "follow me", "check my", "sub4sub", "subscribe to my",
    "dm me", "whatsapp", "follow back", "check out my",
]
_SPAM_URL_PATTERN = re.compile(
    r"(https?://|www\.|\.com|\.ly|t\.me|bit\.ly|youtu\.be)", re.IGNORECASE
)

DAILY_REPLY_LIMIT = 30


def _get_yt_client():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _load_replied() -> set:
    if _REPLIED_FILE.exists():
        try:
            data = json.loads(_REPLIED_FILE.read_text(encoding="utf-8"))
            return set(data)
        except Exception:
            return set()
    return set()


def _save_replied(replied: set) -> None:
    _REPLIED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REPLIED_FILE.write_text(
        json.dumps(sorted(replied), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_spam(text: str) -> bool:
    if len(text.strip()) < 5:
        return True
    if _SPAM_URL_PATTERN.search(text):
        return True
    lower = text.lower()
    for word in _SPAM_WORDS:
        if word in lower:
            return True
    return False


def _get_recent_videos(yt, max_results: int = 20) -> list[dict]:
    try:
        resp = (
            yt.search()
            .list(
                part="snippet",
                forMine=True,
                type="video",
                maxResults=max_results,
                order="date",
            )
            .execute()
        )
        videos = []
        for item in resp.get("items", []):
            vid_id = item.get("id", {}).get("videoId")
            title = item.get("snippet", {}).get("title", "")
            if vid_id:
                videos.append({"id": vid_id, "title": title})
        return videos
    except Exception as e:
        logger.error(f"Error obteniendo videos: {e}")
        return []


def _get_recent_comments(yt, video_id: str, cutoff: datetime) -> list[dict]:
    comments = []
    try:
        resp = (
            yt.commentThreads()
            .list(
                part="snippet",
                videoId=video_id,
                order="time",
                maxResults=20,
            )
            .execute()
        )
        for item in resp.get("items", []):
            top = item.get("snippet", {}).get("topLevelComment", {})
            snip = top.get("snippet", {})
            published_raw = snip.get("publishedAt", "")
            try:
                published = datetime.fromisoformat(
                    published_raw.replace("Z", "+00:00")
                )
            except Exception:
                continue
            if published < cutoff:
                continue
            comment_id = top.get("id")
            text = snip.get("textOriginal") or snip.get("textDisplay", "")
            author = snip.get("authorDisplayName", "")
            if comment_id and text:
                comments.append(
                    {"id": comment_id, "text": text, "author": author}
                )
    except Exception as e:
        logger.warning(f"Error obteniendo comentarios de {video_id}: {e}")
    return comments


def _generate_reply(video_title: str, comment_text: str) -> str:
    from modules.llm_service import call_llm

    prompt = (
        f"Eres el gestor de un canal de YouTube de confesiones dramáticas en español.\n"
        f"Responde este comentario de forma auténtica, empática y que fomente conversación.\n"
        f"Máximo 2 oraciones. En español. Sin hashtags. Sin emojis excesivos (máximo 1).\n\n"
        f"Video: {video_title}\n"
        f"Comentario: {comment_text}"
    )
    return call_llm(prompt, max_tokens=120, temperature=0.85)


def _post_reply(yt, comment_id: str, reply_text: str) -> bool:
    try:
        yt.comments().insert(
            part="snippet",
            body={"snippet": {"parentId": comment_id, "textOriginal": reply_text}},
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"Error publicando reply a {comment_id}: {e}")
        return False


def run_comment_agent() -> dict:
    stats = {
        "videos": 0,
        "comments_processed": 0,
        "replied": 0,
        "skipped_spam": 0,
    }

    try:
        yt = _get_yt_client()
    except Exception as e:
        logger.error(f"Comment agent: no se pudo autenticar con YouTube: {e}")
        return stats

    replied = _load_replied()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    videos = _get_recent_videos(yt, max_results=20)
    stats["videos"] = len(videos)

    if not videos:
        logger.warning("Comment agent: no se encontraron videos recientes")
        return stats

    videos_with_replies: set[str] = set()

    for video in videos:
        if stats["replied"] >= DAILY_REPLY_LIMIT:
            logger.info("Comment agent: límite diario alcanzado")
            break

        video_id = video["id"]
        video_title = video["title"]
        comments = _get_recent_comments(yt, video_id, cutoff)

        for comment in comments:
            if stats["replied"] >= DAILY_REPLY_LIMIT:
                break

            comment_id = comment["id"]
            comment_text = comment["text"]

            stats["comments_processed"] += 1

            if comment_id in replied:
                continue

            if _is_spam(comment_text):
                stats["skipped_spam"] += 1
                logger.debug(f"Spam descartado: {comment_text[:60]}")
                continue

            reply_text = _generate_reply(video_title, comment_text)
            if not reply_text:
                logger.warning(f"LLM no generó respuesta para {comment_id}")
                continue

            success = _post_reply(yt, comment_id, reply_text)
            if success:
                replied.add(comment_id)
                videos_with_replies.add(video_id)
                stats["replied"] += 1
                logger.info(
                    f"Reply publicada en '{video_title[:40]}' → {comment_text[:50]!r}"
                )

    _save_replied(replied)

    if stats["replied"] > 0:
        try:
            from modules.telegram_commander import notify
            notify(
                f"💬 Comment agent: respondidos {stats['replied']} comentarios "
                f"en {len(videos_with_replies)} videos"
            )
        except Exception as e:
            logger.warning(f"No se pudo notificar por Telegram: {e}")

    logger.info(f"Comment agent finalizado: {stats}")
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_comment_agent()
    print(f"Resultado: {result}")
