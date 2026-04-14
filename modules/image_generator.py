"""
image_generator.py — Genera imágenes 1080x1920 para cada escena del script

Dos rutas según el prompt:
  1. Logo/marca detectada  → PIL composite (instantáneo, texto 100% correcto)
  2. Escena visual         → ComfyUI Z-Image Turbo (~20-30s, IA local)
  3. Sin ComfyUI disponible → PIL fallback con gradiente oscuro
"""

import io
import logging
import random
import re
import shutil
import time
import uuid
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import config

logger = logging.getLogger(__name__)

# Resolución nativa de Z-Image Turbo — se escala a 1080x1920 después
# Configurable via SD_NATIVE_RES en .env (768=rápido, 1024=calidad)
def _native_res() -> int:
    return getattr(config, "SD_NATIVE_RES", 768)

def _turbo_steps() -> int:
    return getattr(config, "SD_TURBO_STEPS", 4)


# ─── Fuentes ──────────────────────────────────────────────────────────────────

def _find_font(size: int = 60) -> ImageFont.FreeTypeFont:
    """Carga la mejor fuente TTF disponible en el tamaño pedido."""
    for fp in config.FONTS_DIR.glob("*.ttf"):
        try:
            return ImageFont.truetype(str(fp), size)
        except Exception:
            pass
    for fp in [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/verdanab.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ─── PIL: Logos de marcas y criptomonedas ─────────────────────────────────────

_LOGO_DATA: dict[str, dict] = {
    # Criptomonedas
    "bitcoin":    {"color": (247, 147,  26), "symbol": "₿", "name": "BITCOIN"},
    "btc":        {"color": (247, 147,  26), "symbol": "₿", "name": "BTC"},
    "ethereum":   {"color": ( 98, 126, 234), "symbol": "Ξ", "name": "ETHEREUM"},
    "eth":        {"color": ( 98, 126, 234), "symbol": "Ξ", "name": "ETH"},
    "solana":     {"color": (153,  69, 255), "symbol": "◎", "name": "SOLANA"},
    "sol":        {"color": (153,  69, 255), "symbol": "◎", "name": "SOL"},
    "bnb":        {"color": (243, 186,  47), "symbol": "◈", "name": "BNB"},
    "xrp":        {"color": (  0, 170, 228), "symbol": "✕", "name": "XRP"},
    "ripple":     {"color": (  0, 170, 228), "symbol": "✕", "name": "RIPPLE"},
    "cardano":    {"color": (  0,  51, 173), "symbol": "₳", "name": "CARDANO"},
    "ada":        {"color": (  0,  51, 173), "symbol": "₳", "name": "ADA"},
    "dogecoin":   {"color": (195, 166,  52), "symbol": "Ð", "name": "DOGECOIN"},
    "doge":       {"color": (195, 166,  52), "symbol": "Ð", "name": "DOGE"},
    "polkadot":   {"color": (230,   0, 122), "symbol": "●", "name": "POLKADOT"},
    "dot":        {"color": (230,   0, 122), "symbol": "●", "name": "DOT"},
    "litecoin":   {"color": (191, 187, 187), "symbol": "Ł", "name": "LITECOIN"},
    "ltc":        {"color": (191, 187, 187), "symbol": "Ł", "name": "LTC"},
    "avalanche":  {"color": (232,  65,  66), "symbol": "▲", "name": "AVALANCHE"},
    "avax":       {"color": (232,  65,  66), "symbol": "▲", "name": "AVAX"},
    "polygon":    {"color": (130,  71, 229), "symbol": "◆", "name": "POLYGON"},
    "matic":      {"color": (130,  71, 229), "symbol": "◆", "name": "MATIC"},
    "chainlink":  {"color": ( 55,  91, 210), "symbol": "⬡", "name": "CHAINLINK"},
    "link":       {"color": ( 55,  91, 210), "symbol": "⬡", "name": "LINK"},
    "tether":     {"color": ( 38, 161, 123), "symbol": "₮", "name": "TETHER"},
    "usdt":       {"color": ( 38, 161, 123), "symbol": "₮", "name": "USDT"},
    "usdc":       {"color": ( 39, 117, 202), "symbol": "$", "name": "USDC"},
    # Monedas fiat (inglés y español)
    "dollar":     {"color": (133, 187, 101), "symbol": "$", "name": "DOLLAR"},
    "dolar":      {"color": (133, 187, 101), "symbol": "$", "name": "DÓLAR"},
    "dolares":    {"color": (133, 187, 101), "symbol": "$", "name": "DÓLARES"},
    "usd":        {"color": (133, 187, 101), "symbol": "$", "name": "USD"},
    "euro":       {"color": (  0,  82, 162), "symbol": "€", "name": "EURO"},
    "euros":      {"color": (  0,  82, 162), "symbol": "€", "name": "EUROS"},
    # Exchanges y wallets
    "binance":    {"color": (243, 186,  47), "symbol": "◈", "name": "BINANCE"},
    "coinbase":   {"color": (  0,  82, 255), "symbol": "◉", "name": "COINBASE"},
    "kraken":     {"color": ( 87,  65, 217), "symbol": "◈", "name": "KRAKEN"},
    "kucoin":     {"color": (  0, 168, 107), "symbol": "◈", "name": "KUCOIN"},
    "bybit":      {"color": (247, 166,   0), "symbol": "◈", "name": "BYBIT"},
    "okx":        {"color": (255, 255, 255), "symbol": "◈", "name": "OKX"},
    "uniswap":    {"color": (255,   0, 122), "symbol": "◈", "name": "UNISWAP"},
    "metamask":   {"color": (232, 131,  29), "symbol": "M", "name": "METAMASK"},
    "ledger":     {"color": (200, 200, 200), "symbol": "⬡", "name": "LEDGER"},
    "trezor":     {"color": (  0, 133,  77), "symbol": "⬡", "name": "TREZOR"},
}

# Pre-ordenado de más largo a más corto para que "ethereum" match antes que "eth"
_LOGO_KEYS: list[str] = sorted(_LOGO_DATA, key=len, reverse=True)

# ─── Logos PNG reales (cryptocurrency-icons) ──────────────────────────────────

# Mapeo clave → símbolo en el repo spothq/cryptocurrency-icons
# None = sin PNG disponible (usa fallback círculo mejorado)
_ICON_SYMBOL: dict[str, str | None] = {
    "bitcoin":   "btc",   "btc":       "btc",
    "ethereum":  "eth",   "eth":       "eth",
    "solana":    "sol",   "sol":       "sol",
    "bnb":       "bnb",
    "xrp":       "xrp",   "ripple":    "xrp",
    "cardano":   "ada",   "ada":       "ada",
    "dogecoin":  "doge",  "doge":      "doge",
    "polkadot":  "dot",   "dot":       "dot",
    "litecoin":  "ltc",   "ltc":       "ltc",
    "avalanche": "avax",  "avax":      "avax",
    "polygon":   "matic", "matic":     "matic",
    "chainlink": "link",  "link":      "link",
    "tether":    "usdt",  "usdt":      "usdt",
    "usdc":      "usdc",
    # Sin PNG en cryptocurrency-icons — círculo mejorado
    "dollar":    None, "dolar":  None, "dolares": None,
    "usd":       None, "euro":   None, "euros":   None,
    "binance":   None, "coinbase": None, "kraken": None,
    "kucoin":    None, "bybit":  None, "okx":     None,
    "uniswap":   None, "metamask": None, "ledger": None,
    "trezor":    None,
}

_ICON_BASE_URL = (
    "https://raw.githubusercontent.com/spothq/cryptocurrency-icons"
    "/master/128/color/{symbol}.png"
)
# Logos de exchanges y fiat desde CoinGecko CDN (no están en cryptocurrency-icons)
_COINGECKO_URLS: dict[str, str] = {
    # Exchanges — imagen large (200px) vía coins API cuando disponible
    "binance":  "https://assets.coingecko.com/coins/images/825/large/bnb-icon2_2x.png",   # BNB = Binance
    "coinbase": "https://assets.coingecko.com/markets/images/23/large/Coinbase_Coin_Primary.png",
    "kraken":   "https://assets.coingecko.com/markets/images/29/large/kraken.jpg",
    "kucoin":   "https://assets.coingecko.com/markets/images/61/large/kucoin.jpg",
    "okx":      "https://assets.coingecko.com/markets/images/96/large/WeChat_Image_20220117220452.png",
    "uniswap":  "https://assets.coingecko.com/coins/images/12504/large/uniswap-uni.png",
}
_LOGOS_DIR = config.ASSETS_DIR / "logos"
_logo_cache: dict[str, "Image.Image | None"] = {}


def _get_logo_png(key: str) -> "Image.Image | None":
    """
    Retorna el logo PNG oficial (RGBA).
    Fuentes por prioridad:
      1. Disco local (assets/logos/)
      2. cryptocurrency-icons (GitHub) — coins principales
      3. CoinGecko CDN — exchanges y otros
    Retorna None si ninguna fuente tiene el logo.
    """
    if key in _logo_cache:
        return _logo_cache[key]

    symbol   = _ICON_SYMBOL.get(key)
    cg_url   = _COINGECKO_URLS.get(key)
    filename = f"{symbol or key}.png"

    _LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    local = _LOGOS_DIR / filename

    # ── Cargar desde disco ────────────────────────────────────────────────────
    if local.exists():
        try:
            img = Image.open(str(local)).convert("RGBA")
            _logo_cache[key] = img
            return img
        except Exception:
            local.unlink(missing_ok=True)

    # ── Descargar desde cryptocurrency-icons ──────────────────────────────────
    if symbol:
        url = _ICON_BASE_URL.format(symbol=symbol)
        img = _download_logo(url, local, key)
        if img:
            return img

    # ── Descargar desde CoinGecko CDN ─────────────────────────────────────────
    if cg_url:
        img = _download_logo(cg_url, local, key)
        if img:
            return img

    _logo_cache[key] = None
    return None


def _download_logo(url: str, local: Path, key: str) -> "Image.Image | None":
    """Descarga un logo desde `url`, lo guarda en `local` y lo retorna como RGBA."""
    try:
        resp = requests.get(url, timeout=12)
        if resp.status_code == 200:
            local.write_bytes(resp.content)
            img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            _logo_cache[key] = img
            logger.info(f"Logo descargado: {local.name} ({len(resp.content)//1024}KB)")
            return img
        logger.warning(f"Logo {key}: HTTP {resp.status_code} ({url[:60]})")
    except Exception as e:
        logger.warning(f"No se pudo descargar logo {key}: {e}")
    return None


def _parse_logos(prompt: str) -> list[str]:
    """
    Detecta marcas/cryptos en el prompt → PIL (texto siempre correcto).
    Usa word-boundary para evitar falsos positivos: "sol" no matchea "solar".
    """
    pl = prompt.lower()
    found, seen = [], set()
    for kw in _LOGO_KEYS:
        if re.search(r'\b' + re.escape(kw) + r'\b', pl):
            name = _LOGO_DATA[kw]["name"]
            if name not in seen:
                found.append(kw)
                seen.add(name)
    return found[:4]


def _draw_logo_composite(brands: list[str], output_path: Path) -> str:
    """
    Genera imagen estilo motion graphics profesional — 14 capas.

      1.  Fondo degradado profundo
      2.  Spotlight radial (color promedio de marcas)
      3.  Partículas bokeh deterministas
      4.  Grid tech blockchain
      5.  Anillos ripple/radar por logo
      6.  Glow exterior amplio
      7.  Glow interior (halo tight)
      8.  Glassmorphism card
      9.  Logo PNG (o círculo fallback)
      10. Shine diagonal recortado al alpha del logo
      11. Reflejo inferior (n=1 únicamente)
      12. Badge broadcast (pill gradiente)
      13. Corner bracket accents
      14. Conector "+" estilizado (n=2)
    """
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    n    = len(brands)
    rng  = random.Random(sum(ord(c) for c in "".join(brands)))

    # ── 1. Fondo: degradado azul-negro profundo ───────────────────────────────
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    draw   = ImageDraw.Draw(canvas)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(
            int(2  + 8  * t),
            int(2  + 7  * t),
            int(12 + 22 * t), 255,
        ))

    # ── 2. Spotlight radial global ────────────────────────────────────────────
    avg_col = tuple(
        sum(_LOGO_DATA.get(b, {"color": (150, 150, 150)})["color"][i]
            for b in brands) // n
        for i in range(3)
    )
    spotlight = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sl_d = ImageDraw.Draw(spotlight)
    for r_sp, a_sp in [(700, 28), (500, 22), (320, 15)]:
        sl_d.ellipse([W//2 - r_sp, H//2 - r_sp, W//2 + r_sp, H//2 + r_sp],
                     fill=(*avg_col, a_sp))
    spotlight = spotlight.filter(ImageFilter.GaussianBlur(radius=120))
    canvas = Image.alpha_composite(canvas, spotlight)

    # ── 3. Partículas bokeh (deterministas — mismo brand = mismo patrón) ──────
    bokeh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bk_d  = ImageDraw.Draw(bokeh)
    for _ in range(55):
        bx  = rng.randint(0, W)
        by  = rng.randint(0, H)
        br  = rng.randint(3, 18)
        ba  = rng.randint(18, 75)
        bc  = _LOGO_DATA.get(brands[rng.randint(0, n - 1)],
                              {"color": avg_col})["color"]
        bk_d.ellipse([bx - br, by - br, bx + br, by + br], fill=(*bc, ba))
    bokeh = bokeh.filter(ImageFilter.GaussianBlur(radius=5))
    canvas = Image.alpha_composite(canvas, bokeh)

    # ── 4. Grid tech: líneas + nodos ─────────────────────────────────────────
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(grid)
    gs   = 88
    for x in range(0, W + gs, gs):
        gd.line([(x, 0), (x, H)], fill=(255, 255, 255, 12), width=1)
    for y in range(0, H + gs, gs):
        gd.line([(0, y), (W, y)], fill=(255, 255, 255, 12), width=1)
    for xi in range(0, W + gs, gs):
        for yi in range(0, H + gs, gs):
            if (xi // gs + yi // gs) % 3 == 0:
                gd.ellipse([xi - 3, yi - 3, xi + 3, yi + 3],
                           fill=(255, 255, 255, 30))
    canvas = Image.alpha_composite(canvas, grid)

    # ── Layout según cantidad de marcas ───────────────────────────────────────
    if n == 1:
        slots     = [(W // 2, H // 2 - 60)]
        logo_size = 480
        card_w    = 620
        card_h    = 720
    elif n == 2:
        slots     = [(W // 3, H // 2), (2 * W // 3, H // 2)]
        logo_size = 340
        card_w    = 390
        card_h    = 530
    elif n == 3:
        slots     = [(W // 4, H // 2), (W // 2, H // 2), (3 * W // 4, H // 2)]
        logo_size = 260
        card_w    = 290
        card_h    = 420
    else:
        slots     = [(W//4, H//3), (3*W//4, H//3),
                     (W//4, 2*H//3), (3*W//4, 2*H//3)]
        logo_size = 230
        card_w    = 260
        card_h    = 380

    badge_font = _find_font(max(36, logo_size // 8))

    for (cx, cy), key in zip(slots, brands):
        d   = _LOGO_DATA.get(key, {"color": (150, 150, 150),
                                   "symbol": "?", "name": key.upper()})
        col = d["color"]

        # ── 5. Anillos ripple/radar ────────────────────────────────────────────
        rings = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        rr_d  = ImageDraw.Draw(rings)
        for i, factor in enumerate([0.65, 0.92, 1.28, 1.65, 2.08]):
            rr    = int(logo_size * factor)
            r_alp = max(5, 55 - i * 10)
            r_thk = max(1, 3 - i)
            rr_d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                         outline=(*col, r_alp), width=r_thk)
        rings = rings.filter(ImageFilter.GaussianBlur(radius=2))
        canvas = Image.alpha_composite(canvas, rings)

        # ── 6. Glow exterior amplio ────────────────────────────────────────────
        glow_out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        go_d     = ImageDraw.Draw(glow_out)
        for g_rad, g_alp in [
            (int(logo_size * 1.8), 55),
            (int(logo_size * 1.3), 42),
            (int(logo_size * 0.9), 28),
        ]:
            go_d.ellipse([cx - g_rad, cy - g_rad, cx + g_rad, cy + g_rad],
                         fill=(*col, g_alp))
        glow_out = glow_out.filter(ImageFilter.GaussianBlur(radius=55))
        canvas = Image.alpha_composite(canvas, glow_out)

        # ── 7. Glow interior (halo tight) ─────────────────────────────────────
        glow_in = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gi_d    = ImageDraw.Draw(glow_in)
        hr = logo_size // 2 + 30
        gi_d.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=(*col, 70))
        glow_in = glow_in.filter(ImageFilter.GaussianBlur(radius=18))
        canvas  = Image.alpha_composite(canvas, glow_in)

        # ── 8. Glassmorphism card ──────────────────────────────────────────────
        logo_y = cy - 35
        x0 = cx - card_w // 2
        y0 = cy - card_h // 2
        x1 = cx + card_w // 2
        y1 = cy + card_h // 2

        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd   = ImageDraw.Draw(card)
        cd.rounded_rectangle([x0, y0, x1, y1], radius=38,
                              fill=(8, 10, 24, 160))
        cd.rounded_rectangle([x0, y0, x1, y1], radius=38,
                              outline=(255, 255, 255, 40), width=1)
        # Accent bar superior
        cd.rounded_rectangle([x0 + 6, y0 + 6, x1 - 6, y0 + 54], radius=32,
                              fill=(*col, 80))
        # Left neon stripe
        cd.rounded_rectangle([x0, y0 + 24, x0 + 5, y1 - 24], radius=4,
                              fill=(*col, 210))
        # Interior shine highlight
        cd.rounded_rectangle([x0 + 6, y0 + 6, x1 - 6, y0 + 55], radius=34,
                              fill=(255, 255, 255, 18))
        canvas = Image.alpha_composite(canvas, card)

        # ── 9. Logo PNG real (o círculo fallback) ──────────────────────────────
        logo_png = _get_logo_png(key)
        px = cx - logo_size // 2
        py = logo_y - logo_size // 2

        if logo_png is not None:
            sized = logo_png.resize((logo_size, logo_size), Image.LANCZOS)
            canvas.paste(sized, (px, py), sized)

            # ── 10. Shine diagonal recortado al alpha del logo ─────────────────
            shine = Image.new("RGBA", (logo_size, logo_size), (0, 0, 0, 0))
            sh_d  = ImageDraw.Draw(shine)
            for sx in range(0, logo_size, 6):
                sa = max(0, int(155 * (
                    1 - abs(sx - logo_size * 0.35) / (logo_size * 0.45)
                )))
                sh_d.line([(sx, 0), (sx, logo_size)],
                          fill=(255, 255, 255, sa), width=3)
            shine = shine.rotate(-38, expand=False)
            shine = shine.filter(ImageFilter.GaussianBlur(radius=3))
            logo_alpha = sized.split()[3]
            shine_alpha = ImageChops.multiply(shine.split()[3], logo_alpha)
            shine.putalpha(shine_alpha)
            shine_full = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            shine_full.paste(shine, (px, py), shine)
            canvas = Image.alpha_composite(canvas, shine_full)

            # ── 11. Reflejo inferior (solo n=1) ────────────────────────────────
            if n == 1:
                reflect = sized.transpose(Image.FLIP_TOP_BOTTOM)
                fade_mask = Image.new("L", (logo_size, logo_size), 0)
                fm_d = ImageDraw.Draw(fade_mask)
                half = logo_size // 2
                for fy in range(half):
                    fm_d.line([(0, fy), (logo_size, fy)],
                              fill=int(65 * (1 - fy / half)))
                reflect.putalpha(fade_mask)
                ry = logo_y + logo_size // 2 + 8
                ref_full = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ref_full.paste(reflect, (px, ry), reflect)
                canvas = Image.alpha_composite(canvas, ref_full)
        else:
            # Fallback: círculo mejorado + símbolo Unicode
            r      = logo_size // 2
            circ   = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            circ_d = ImageDraw.Draw(circ)
            circ_d.ellipse([cx - r, logo_y - r, cx + r, logo_y + r],
                           fill=(*col, 255), outline=(255, 255, 255, 200), width=5)
            canvas = Image.alpha_composite(canvas, circ)
            f_sym  = _find_font(int(r * 0.70))
            sym    = d["symbol"]
            sym_d  = ImageDraw.Draw(canvas)
            sb     = sym_d.textbbox((0, 0), sym, font=f_sym)
            sym_d.text(
                (cx - (sb[2] - sb[0]) // 2 - sb[0],
                 logo_y - (sb[3] - sb[1]) // 2 - sb[1]),
                sym, fill=(255, 255, 255, 255), font=f_sym,
            )

        # ── 12. Badge broadcast (pill con color de marca) ──────────────────────
        name   = d["name"]
        bd_d   = ImageDraw.Draw(canvas)
        nb     = bd_d.textbbox((0, 0), name, font=badge_font)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        bpad_x, bpad_y = 32, 14
        bx0 = cx - nw // 2 - bpad_x
        by0 = logo_y + logo_size // 2 + 32
        bx1 = cx + nw // 2 + bpad_x
        by1 = by0 + nh + bpad_y * 2

        pill_lyr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(pill_lyr).rounded_rectangle(
            [bx0, by0, bx1, by1], radius=28,
            fill=(*col, 210), outline=(255, 255, 255, 65), width=2,
        )
        canvas = Image.alpha_composite(canvas, pill_lyr)
        ImageDraw.Draw(canvas).text(
            (cx - nw // 2 - nb[0], by0 + bpad_y - nb[1]),
            name, fill=(255, 255, 255, 255), font=badge_font,
        )

        # ── 13. Corner bracket accents ─────────────────────────────────────────
        brk   = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bk_d  = ImageDraw.Draw(brk)
        arm   = 28
        thk   = 3
        ba_c  = (*col, 200)
        for (bkx, bky), (sx, sy) in zip(
            [(x0, y0), (x1, y0), (x0, y1), (x1, y1)],
            [(1, 1),   (-1, 1),  (1, -1),  (-1, -1)],
        ):
            bk_d.line([(bkx, bky), (bkx + sx * arm, bky)],
                      fill=ba_c, width=thk)
            bk_d.line([(bkx, bky), (bkx, bky + sy * arm)],
                      fill=ba_c, width=thk)
        canvas = Image.alpha_composite(canvas, brk)

    # ── 14. Conector "+" estilizado (n=2) ─────────────────────────────────────
    if n == 2:
        f_plus = _find_font(100)
        pd     = ImageDraw.Draw(canvas)
        pb     = pd.textbbox((0, 0), "+", font=f_plus)
        c1     = _LOGO_DATA.get(brands[0], {"color": (200, 200, 200)})["color"]
        c2     = _LOGO_DATA.get(brands[1], {"color": (200, 200, 200)})["color"]
        avg2   = tuple((a + b) // 2 for a, b in zip(c1, c2))
        pcx    = W // 2 - (pb[2] - pb[0]) // 2 - pb[0]
        pcy    = H // 2 - (pb[3] - pb[1]) // 2 - pb[1]
        pd.text((pcx + 4, pcy + 4), "+", fill=(0, 0, 0, 160), font=f_plus)
        pd.text((pcx, pcy),         "+", fill=(*avg2, 240),    font=f_plus)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(str(output_path), "PNG", optimize=True)
    names = [_LOGO_DATA.get(b, {}).get("name", b) for b in brands]
    logger.info(f"Logo composite → {output_path.name} ({' + '.join(names)})")
    return str(output_path)


# ─── ComfyUI Z-Image Turbo ────────────────────────────────────────────────────

def detect_sd_backend() -> str:
    """Detecta qué backend de SD está disponible."""
    if config.SD_BACKEND in ("comfyui", "a1111"):
        url = config.SD_COMFYUI_URL if config.SD_BACKEND == "comfyui" else config.SD_A1111_URL
        logger.info(f"Backend forzado por config: {config.SD_BACKEND} @ {url}")
        return config.SD_BACKEND

    for endpoint in ["/system_stats", "/queue", "/"]:
        try:
            r = requests.get(f"{config.SD_COMFYUI_URL}{endpoint}", timeout=5)
            if r.status_code in (200, 404):
                logger.info(f"ComfyUI detectado en {config.SD_COMFYUI_URL}")
                return "comfyui"
        except Exception:
            pass

    try:
        r = requests.get(f"{config.SD_A1111_URL}/sdapi/v1/sd-models", timeout=5)
        if r.status_code == 200:
            logger.info(f"A1111 detectado en {config.SD_A1111_URL}")
            return "a1111"
    except Exception:
        pass

    logger.warning("Ningún backend SD detectado — usando fallback PIL")
    return "none"


# Negative prompt words to replace if wrong gender is detected
_FEMALE_WORDS = {"woman", "girl", "female", "she", "her", "lady", "wife", "girlfriend"}
_MALE_WORDS   = {"man", "boy", "male", "he", "his", "guy", "husband", "boyfriend"}


def _enrich_prompt(raw: str, character_description: str = "", gender: str = "") -> str:
    """
    Construye el prompt final para SD:
    1. Antepone character_description para consistencia visual del personaje
    2. Corrige el género si el prompt menciona el sexo equivocado
    3. Añade estilo dramático/cinematográfico

    Args:
        raw:                   Prompt base generado por Ollama
        character_description: Descripción física del narrador (viene del script)
        gender:                "female" | "male" — género del narrador
    """
    prompt = raw.strip()

    # 1. Corregir género si el prompt menciona el sexo contrario
    if gender == "male":
        for fw in _FEMALE_WORDS:
            # reemplaza "woman" → "man", "girl" → "man", etc. (word boundary)
            import re as _re
            prompt = _re.sub(rf'\b{fw}\b', "man", prompt, flags=_re.IGNORECASE)
    elif gender == "female":
        for mw in _MALE_WORDS:
            import re as _re
            prompt = _re.sub(rf'\b{mw}\b', "woman", prompt, flags=_re.IGNORECASE)

    # 2. Anteponer descripción del personaje para consistencia entre escenas
    if character_description:
        # Sólo añadir si el prompt no la incluye ya
        char_lower = character_description.lower()
        if char_lower[:20] not in prompt.lower():
            prompt = f"{character_description}, {prompt}"

    # 3. Sufijo de estilo cinematográfico/dramático
    return (
        f"{prompt}, "
        "dramatic lighting, extreme emotional tension, intense shadows, "
        "hyper-realistic, raw and visceral, high contrast, moody atmosphere, "
        "cinematic composition, 4k, sharp focus, no text, no watermark, "
        "photorealistic, ultra detailed"
    )


def _build_z_image_workflow(prompt: str, seed: int) -> dict:
    """Workflow Z-Image Turbo para ComfyUI API."""
    return {
        "client_id": str(uuid.uuid4()),
        "prompt": {
            "9":     {"class_type": "SaveImage",
                      "inputs": {"filename_prefix": "csf_", "images": ["57:8", 0]}},
            "57:30": {"class_type": "CLIPLoader",
                      "inputs": {"clip_name": "qwen_3_4b.safetensors",
                                 "type": "lumina2", "device": "default"}},
            "57:29": {"class_type": "VAELoader",
                      "inputs": {"vae_name": "ae.safetensors"}},
            "57:33": {"class_type": "ConditioningZeroOut",
                      "inputs": {"conditioning": ["57:27", 0]}},
            "57:8":  {"class_type": "VAEDecode",
                      "inputs": {"samples": ["57:3", 0], "vae": ["57:29", 0]}},
            "57:28": {"class_type": "UNETLoader",
                      "inputs": {"unet_name": "z_image_turbo_bf16.safetensors",
                                 "weight_dtype": "default"}},
            "57:27": {"class_type": "CLIPTextEncode",
                      "inputs": {"text": prompt, "clip": ["57:30", 0]}},
            "57:13": {"class_type": "EmptySD3LatentImage",
                      "inputs": {"width": _native_res(), "height": _native_res(), "batch_size": 1}},
            "57:11": {"class_type": "ModelSamplingAuraFlow",
                      "inputs": {"shift": 3, "model": ["57:28", 0]}},
            "57:3":  {"class_type": "KSampler",
                      "inputs": {
                          "seed": seed, "steps": _turbo_steps(), "cfg": 1,
                          "sampler_name": "res_multistep", "scheduler": "simple",
                          "denoise": 1, "model": ["57:11", 0],
                          "positive": ["57:27", 0], "negative": ["57:33", 0],
                          "latent_image": ["57:13", 0],
                      }},
        },
    }


def _comfyui_clear_queue() -> None:
    """Limpia la cola de ComfyUI antes de empezar."""
    try:
        queue = requests.get(f"{config.SD_COMFYUI_URL}/queue", timeout=5).json()
        ids   = ([item[1] for item in queue.get("queue_pending", [])]
                 + [item[1] for item in queue.get("queue_running", [])])
        if ids:
            requests.post(f"{config.SD_COMFYUI_URL}/queue", json={"delete": ids}, timeout=5)
            logger.info(f"Cola ComfyUI limpiada: {len(ids)} item(s)")
        requests.post(f"{config.SD_COMFYUI_URL}/interrupt", timeout=5)
    except Exception as e:
        logger.debug(f"_comfyui_clear_queue: {e}")



def _comfyui_submit(prompt: str, character_description: str = "", gender: str = "", seed: int | None = None) -> str:
    """Envía un prompt a ComfyUI y retorna el prompt_id (sin esperar)."""
    if seed is None:
        seed = random.randint(0, 2**31 - 1)
    workflow = _build_z_image_workflow(_enrich_prompt(prompt, character_description, gender), seed=seed)
    logger.info(f"Z-Image Turbo submit | seed={seed} | '{prompt[:60]}'")
    resp = requests.post(f"{config.SD_COMFYUI_URL}/prompt", json=workflow, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"ComfyUI HTTP {resp.status_code}: {resp.text[:200]}")
    prompt_id = resp.json().get("prompt_id")
    if not prompt_id:
        raise ValueError(f"Sin prompt_id: {resp.text[:100]}")
    return prompt_id


def _comfyui_download(outputs: dict, output_path: Path) -> str:
    """Descarga la imagen de los outputs de ComfyUI y la guarda como portrait."""
    for node_output in outputs.values():
        imgs = node_output.get("images")
        if not imgs:
            continue
        info  = imgs[0]
        img_r = requests.get(
            f"{config.SD_COMFYUI_URL}/view",
            params={"filename": info["filename"],
                    "subfolder": info.get("subfolder", ""),
                    "type":      info.get("type", "output")},
            timeout=60,
        )
        img_r.raise_for_status()
        img = Image.open(io.BytesIO(img_r.content)).convert("RGB")
        img = _to_portrait(img)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "PNG", optimize=True)
        logger.info(f"Z-Image Turbo → {output_path.name} ({config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT})")
        return str(output_path)
    raise RuntimeError("ComfyUI completó sin imágenes en el output.")


# ─── Resolución portrait ──────────────────────────────────────────────────────

def _to_portrait(img: Image.Image) -> Image.Image:
    """Escala a 1080x1920 preservando ratio. Rellena con fondo borroso si es landscape."""
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    src_r = img.width / img.height
    tgt_r = W / H

    if abs(src_r - tgt_r) < 0.05:
        return img.resize((W, H), Image.LANCZOS)

    if src_r < tgt_r:
        # Más alta → escalar al ancho, recortar verticalmente
        scale  = W / img.width
        new_h  = int(img.height * scale)
        scaled = img.resize((W, new_h), Image.LANCZOS)
        top    = (new_h - H) // 2
        return scaled.crop((0, top, W, top + H))
    else:
        # Más ancha → relleno con fondo borroso oscuro
        scale  = W / img.width
        new_h  = int(img.height * scale)
        scaled = img.resize((W, new_h), Image.LANCZOS)
        bg     = img.resize((W, H), Image.LANCZOS)
        bg     = bg.filter(ImageFilter.GaussianBlur(radius=40))
        dark   = Image.new("RGBA", bg.size, (0, 0, 0, 140))
        bg     = Image.alpha_composite(bg.convert("RGBA"), dark).convert("RGB")
        y_off  = (H - new_h) // 2
        bg.paste(scaled, (0, y_off))
        return bg


# ─── Fallback PIL ─────────────────────────────────────────────────────────────

def _pil_fallback(text: str, output_path: Path, idx: int = 0) -> str:
    """Imagen de fallback: gradiente oscuro con texto de la escena."""
    palettes = [
        ((6, 6, 20),  (16, 16, 50)),
        ((20, 4, 4),  (50, 8,  8)),
        ((4, 20, 4),  (8,  45, 8)),
        ((4, 8, 20),  (8,  20, 55)),
        ((20, 10, 4), (50, 25, 8)),
    ]
    top, bot = palettes[idx % len(palettes)]
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(
            int(top[0] + (bot[0]-top[0])*t),
            int(top[1] + (bot[1]-top[1])*t),
            int(top[2] + (bot[2]-top[2])*t),
        ))

    if text:
        font  = _find_font(52)
        words = text.split()
        lines, current = [], []
        for word in words:
            test = " ".join(current + [word])
            if draw.textbbox((0, 0), test, font=font)[2] > W - 80 and current:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))

        line_h  = 68
        y_start = H // 2 - len(lines) * line_h // 2
        for i, line in enumerate(lines):
            bb = draw.textbbox((0, 0), line, font=font)
            x  = (W - (bb[2]-bb[0])) // 2
            y  = y_start + i * line_h
            draw.text((x+2, y+2), line, fill=(0,  0,  0),   font=font)
            draw.text((x,   y),   line, fill=(255,255,255), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    logger.info(f"PIL fallback → {output_path.name}")
    return str(output_path)


# ─── API pública ──────────────────────────────────────────────────────────────

def generate_images(
    scenes: list[dict],
    output_dir: str | Path,
    character_description: str = "",
    gender: str = "",
    post_seed: int | None = None,
) -> list[str]:
    """
    Genera imágenes para todas las escenas del script.
    character_description y gender se inyectan en cada prompt para
    mantener consistencia visual del personaje y género correcto.

    Optimizaciones:
      - Deduplicación de prompts: escenas con el mismo prompt comparten la imagen generada
      - Submit-all-then-poll: todos los prompts ComfyUI se encolan a la vez y se sondean en paralelo

    Rutas por escena:
      1. Prompt con logo/marca → PIL composite (instantáneo, texto correcto)
      2. Prompt visual         → ComfyUI batch (dedup + poll simultáneo)
      3. Sin ComfyUI           → PIL fallback con gradiente oscuro
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    backend     = detect_sd_backend()
    total       = len(scenes)
    image_paths: list[str | None] = [None] * total

    if backend == "comfyui":
        _comfyui_clear_queue()

    max_unique = getattr(config, "SD_MAX_IMAGES", 12)
    logger.info(
        f"Generando {total} imágenes | backend='{backend}' | "
        f"res={_native_res()}px | steps={_turbo_steps()} | max_unique={max_unique}"
    )
    start = time.time()

    # ── Paso 1: PIL (logos e fallback) + clasificar cuáles van a ComfyUI ─────
    # prompt_cache: prompt_str → ruta PNG ya generada (evita duplicados PIL también)
    prompt_cache: dict[str, str] = {}
    # comfyui_jobs: prompt → [(scene_idx, output_path)] — agrupa duplicados
    comfyui_jobs: dict[str, list[tuple[int, Path]]] = {}
    # unique_count: cuántos prompts ÚNICOS llevamos (para el cap de SD_MAX_IMAGES)
    unique_prompts_seen: list[str] = []

    for i, scene in enumerate(scenes):
        out = output_dir / f"scene_{i:03d}.png"

        if out.exists():
            logger.info(f"Imagen cacheada: {out.name}")
            image_paths[i] = str(out)
            continue

        raw    = scene.get("image_prompt") or scene.get("text") or "dark background"
        prompt = raw.strip()

        # ── Cap de imágenes únicas: si ya tenemos max_unique prompts distintos,
        #    reciclar la imagen más cercana ya encolada (evita N×SD_TURBO_STEPS extra)
        if prompt not in unique_prompts_seen and prompt not in comfyui_jobs:
            if len(unique_prompts_seen) >= max_unique and unique_prompts_seen:
                # Asignar al primer prompt ya registrado (round-robin)
                recycled = unique_prompts_seen[i % len(unique_prompts_seen)]
                comfyui_jobs.setdefault(recycled, []).append((i, out))
                logger.debug(f"Escena {i}: prompt reciclado (límite {max_unique} alcanzado)")
                continue
            unique_prompts_seen.append(prompt)

        # ── Ruta 1: PIL logos ─────────────────────────────────────────────────
        logos = _parse_logos(prompt)
        if logos:
            if prompt in prompt_cache:
                shutil.copy2(prompt_cache[prompt], str(out))
                image_paths[i] = str(out)
                logger.info(f"Logo reutilizado: {out.name}")
            else:
                try:
                    result = _draw_logo_composite(logos, out)
                    image_paths[i] = result
                    prompt_cache[prompt] = result
                except Exception as e:
                    logger.error(f"Error logo escena {i}: {e}")
                    image_paths[i] = _pil_fallback(scene.get("text", ""), out, i)
            continue

        # ── Ruta 2: ComfyUI — acumular para batch ─────────────────────────────
        if backend == "comfyui":
            comfyui_jobs.setdefault(prompt, []).append((i, out))
            continue

        # ── Ruta 3: PIL fallback ──────────────────────────────────────────────
        try:
            image_paths[i] = _pil_fallback(scene.get("text", prompt), out, i)
        except Exception as e:
            logger.error(f"Error fallback escena {i}: {e}")
            img = Image.new("RGB", (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), (8, 8, 20))
            img.save(str(out))
            image_paths[i] = str(out)

    # ── Paso 2: ComfyUI batch — submit todos, luego poll simultáneo ───────────
    if comfyui_jobs:
        n_unique = len(comfyui_jobs)
        n_total  = sum(len(v) for v in comfyui_jobs.values())
        if n_unique < n_total:
            logger.info(
                f"Dedup ComfyUI: {n_total} escenas → {n_unique} prompts únicos "
                f"(ahorro: {n_total - n_unique} generaciones)"
            )

        # Enviar todos los prompts a la cola de ComfyUI de una vez
        # pending: [(prompt_id, prompt, [(scene_idx, out_path)])]
        pending: list[tuple[str, str, list[tuple[int, Path]]]] = []

        for prompt_idx, (prompt, scene_list) in enumerate(comfyui_jobs.items()):
            try:
                # Seed determinístico: mismo video = misma persona en todas las escenas
                # post_seed=None → aleatorio (modo legacy compatible)
                scene_seed = None
                if post_seed is not None:
                    scene_seed = (post_seed + prompt_idx * 137) % (2**31)
                prompt_id = _comfyui_submit(
                    prompt,
                    character_description=character_description,
                    gender=gender,
                    seed=scene_seed,
                )
                logger.info(f"Encolado {prompt_id[:8]}… → '{prompt[:55]}'")
                pending.append((prompt_id, prompt, scene_list))
            except Exception as e:
                logger.error(f"Error enviando '{prompt[:55]}': {e}")
                for idx, out in scene_list:
                    image_paths[idx] = _pil_fallback(scenes[idx].get("text", ""), out, idx)

        # Sondear todos los prompt_ids hasta que terminen o se agote el timeout
        timeout     = 120
        poll_elapsed = 0

        while pending and poll_elapsed < timeout:
            time.sleep(1)
            poll_elapsed += 1

            still_pending = []
            for prompt_id, prompt, scene_list in pending:
                try:
                    history = requests.get(
                        f"{config.SD_COMFYUI_URL}/history/{prompt_id}", timeout=10
                    ).json()
                except Exception as e:
                    logger.warning(f"Polling {prompt_id[:8]}: {e}")
                    still_pending.append((prompt_id, prompt, scene_list))
                    continue

                if prompt_id not in history:
                    still_pending.append((prompt_id, prompt, scene_list))
                    continue

                entry  = history[prompt_id]
                status = entry.get("status", {})

                if status.get("status_str") == "error":
                    logger.error(f"ComfyUI error {prompt_id[:8]}: {status.get('messages', [])}")
                    for idx, out in scene_list:
                        image_paths[idx] = _pil_fallback(scenes[idx].get("text", ""), out, idx)
                    continue

                outputs = entry.get("outputs", {})
                if not outputs:
                    still_pending.append((prompt_id, prompt, scene_list))
                    continue

                # Imagen lista — descargar y copiar para escenas duplicadas
                primary_idx, primary_out = scene_list[0]
                try:
                    result = _comfyui_download(outputs, primary_out)
                    image_paths[primary_idx] = result
                    for idx, out in scene_list[1:]:
                        shutil.copy2(result, str(out))
                        image_paths[idx] = str(out)
                        logger.info(f"Imagen reutilizada: {out.name}")
                except Exception as e:
                    logger.error(f"Error descargando {prompt_id[:8]}: {e}")
                    for idx, out in scene_list:
                        image_paths[idx] = _pil_fallback(scenes[idx].get("text", ""), out, idx)

            pending = still_pending
            if pending and poll_elapsed % 15 == 0:
                logger.info(f"Esperando {len(pending)} imagen(es)… {poll_elapsed}s/{timeout}s")

        # Timeout — usar fallback PIL para las que no terminaron
        for prompt_id, prompt, scene_list in pending:
            logger.error(f"Timeout: {prompt_id[:8]} no completó en {timeout}s")
            for idx, out in scene_list:
                image_paths[idx] = _pil_fallback(scenes[idx].get("text", ""), out, idx)

    # ── Paso 3: Safety net — ningún slot debe quedar None ─────────────────────
    for i, path in enumerate(image_paths):
        if path is None:
            out = output_dir / f"scene_{i:03d}.png"
            logger.warning(f"Slot {i} sin imagen, generando fallback de emergencia")
            try:
                image_paths[i] = _pil_fallback(scenes[i].get("text", ""), out, i)
            except Exception:
                img = Image.new("RGB", (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), (8, 8, 20))
                img.save(str(out))
                image_paths[i] = str(out)

    elapsed = time.time() - start
    ok_count = sum(1 for p in image_paths if p)
    logger.info(f"{ok_count}/{total} imágenes listas en {elapsed:.1f}s ({elapsed/total:.1f}s/img)")
    return image_paths
