"""
youtube_uploader.py — Sube videos a YouTube Studio con Selenium (comportamiento humano)

Técnicas anti-detección:
  - undetected_chromedriver para evitar detección de bots
  - Perfil Chrome persistente (mantiene sesión/cookies)
  - Delays aleatorios entre acciones (1.5-4.0s)
  - Escritura letra por letra con delays variables
  - Scroll y movimiento de mouse antes de cada click

Ejemplo de uso:
  from modules.youtube_uploader import upload_to_youtube
  success = upload_to_youtube("video.mp4", "Nunca debí revisar su celular...", "descripción...", ["#confesion"])
"""

import logging
import random
import sys
import time
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

# ─── Selectores de YouTube Studio ─────────────────────────────────────────────
# NOTA: Los selectores de YouTube cambian con frecuencia.
# Si alguno falla, inspeccionar el elemento y actualizar aquí.

SELECTORS = {
    # Página de login
    "email_input": "input[type='email']",
    "email_next": "#identifierNext",
    "password_input": "input[type='password']",
    "password_next": "#passwordNext",

    # Studio - botón crear
    "create_btn": "ytcp-button#create-icon",
    "upload_option": "tp-yt-paper-item#text-item-0",  # "Subir videos"

    # Modal de upload
    "file_input": "input[type='file']",
    "title_input": "#title-textarea",
    "description_input": "#description-textarea",

    # Audiencia
    "not_for_kids": "#radioLabel:nth-child(2)",  # No, no es para niños
    "kids_radio_2": "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",

    # Visibilidad
    "public_radio": "tp-yt-paper-radio-button[name='PUBLIC']",

    # Siguiente / Guardar
    "next_btn": "ytcp-button#next-button",
    "save_btn": "ytcp-button#done-button",

    # Confirmación
    "upload_complete": "ytcp-video-upload-progress",
    "close_btn": "ytcp-button#close-button",
}


class HumanBehavior:
    """Simula comportamiento humano realista en Selenium para evitar detección de bots."""

    def __init__(self, driver):
        self.driver = driver

    def random_delay(self, min_s: float = 2.0, max_s: float = 6.0) -> None:
        """Pausa aleatoria entre acciones — distribución triangular para más realismo."""
        delay = random.triangular(min_s, max_s, (min_s + max_s) / 2)
        time.sleep(delay)

    def read_pause(self) -> None:
        """Pausa larga simulando que el usuario lee la pantalla antes de actuar."""
        time.sleep(random.triangular(2.5, 7.0, 4.0))

    def random_mouse_move(self) -> None:
        """Mueve el mouse a una posición aleatoria de la página (simula que el usuario mira)."""
        try:
            w = self.driver.execute_script("return window.innerWidth")
            h = self.driver.execute_script("return window.innerHeight")
            x = random.randint(int(w * 0.1), int(w * 0.9))
            y = random.randint(int(h * 0.1), int(h * 0.8))
            ActionChains(self.driver).move_by_offset(0, 0).perform()  # reset
            ActionChains(self.driver).move_by_offset(x // 4, y // 4).perform()
            time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass

    def random_scroll(self) -> None:
        """Scroll aleatorio arriba/abajo simulando que el usuario revisa la página."""
        try:
            direction = random.choice([1, -1])
            amount    = random.randint(100, 400)
            self.driver.execute_script(f"window.scrollBy({{top: {direction * amount}, behavior: 'smooth'}})")
            time.sleep(random.uniform(0.8, 2.0))
        except Exception:
            pass

    def move_to_element(self, element) -> None:
        """Mueve el mouse al elemento con trayectoria no lineal."""
        try:
            actions = ActionChains(self.driver)
            # Dos movimientos intermedios para simular trayectoria natural
            actions.move_by_offset(random.randint(-30, 30), random.randint(-20, 20))
            actions.move_to_element(element)
            actions.move_by_offset(random.randint(-4, 4), random.randint(-4, 4))
            actions.perform()
            time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass

    def scroll_to_element(self, element) -> None:
        """Scroll suave hasta el elemento y pausa como si lo leyera."""
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                element
            )
            time.sleep(random.uniform(0.6, 1.5))
        except Exception:
            pass

    def human_click(self, element) -> None:
        """Click humano: scroll → pausa de lectura → mover mouse → click."""
        self.scroll_to_element(element)
        # Simular que el usuario lee/verifica antes de hacer click
        time.sleep(random.uniform(0.5, 1.8))
        self.move_to_element(element)
        element.click()
        self.random_delay(1.0, 3.0)

    def human_type(self, element, text: str, clear_first: bool = True) -> None:
        """
        Escribe texto imitando velocidad humana real (~50-70 WPM).
        - Velocidad por carácter: 0.08-0.32s (vs 0.05-0.15s anterior)
        - Pausas de "pensar" frecuentes: cada 8-20 chars
        - 2% de probabilidad de error tipográfico con corrección
        - Pausa más larga en espacios (separación natural entre palabras)
        """
        self.human_click(element)
        # Pausa larga antes de empezar a escribir (el usuario lee el campo)
        time.sleep(random.uniform(1.0, 2.5))

        if clear_first:
            element.send_keys(Keys.CONTROL + "a")
            time.sleep(random.uniform(0.3, 0.6))
            element.send_keys(Keys.DELETE)
            time.sleep(random.uniform(0.4, 0.8))

        chars_since_pause = 0
        next_pause_at     = random.randint(8, 20)

        for char in text:
            # Error tipográfico ocasional (2%) con corrección inmediata
            if random.random() < 0.02 and char.isalpha():
                wrong = random.choice("qwertyuiopasdfghjklzxcvbnm")
                element.send_keys(wrong)
                time.sleep(random.uniform(0.1, 0.3))
                element.send_keys(Keys.BACKSPACE)
                time.sleep(random.uniform(0.15, 0.35))

            element.send_keys(char)

            # Velocidad base: ~50-70 WPM ≈ 0.08-0.32s por carácter
            if char == " ":
                # Pausa ligeramente más larga entre palabras
                time.sleep(random.uniform(0.12, 0.35))
            elif char in ".,;:!?\n":
                # Pausa más larga en puntuación (el usuario "piensa" el siguiente)
                time.sleep(random.uniform(0.2, 0.6))
            else:
                time.sleep(random.uniform(0.08, 0.28))

            chars_since_pause += 1
            if chars_since_pause >= next_pause_at:
                # Pausa de "pensar/leer" cada cierta cantidad de chars
                time.sleep(random.uniform(0.5, 2.2))
                chars_since_pause = 0
                next_pause_at     = random.randint(8, 22)


def _init_driver() -> uc.Chrome:
    """
    Inicializa undetected_chromedriver con perfil persistente.

    Returns:
        Instancia de Chrome con configuración anti-detección
    """
    profile_dir = Path(config.CHROME_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")

    # Tamaño de ventana realista
    options.add_argument("--window-size=1920,1080")

    driver = uc.Chrome(options=options, use_subprocess=True)

    # Ocultar características de WebDriver
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    return driver


def _is_logged_in(driver, wait: WebDriverWait) -> bool:
    """
    Verifica si ya hay una sesión activa en YouTube Studio.

    Returns:
        True si hay sesión activa
    """
    try:
        # Si estamos en Studio, estamos logueados
        current_url = driver.current_url
        if "studio.youtube.com" in current_url and "accounts.google.com" not in current_url:
            return True

        # Buscar botón de cuenta (indica sesión activa)
        driver.find_element(By.CSS_SELECTOR, "button[aria-label*='Account']")
        return True
    except NoSuchElementException:
        return False
    except Exception:
        return False


def _login(driver, human: HumanBehavior, wait: WebDriverWait) -> None:
    """
    Realiza el login en Google/YouTube con comportamiento humano.

    Args:
        driver: WebDriver
        human: Instancia de HumanBehavior
        wait: WebDriverWait configurado
    """
    if not config.YOUTUBE_EMAIL or not config.YOUTUBE_PASSWORD:
        raise ValueError(
            "YOUTUBE_EMAIL y YOUTUBE_PASSWORD no están configurados en .env"
        )

    logger.info("Iniciando login en Google...")

    # Email
    email_field = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["email_input"]))
    )
    human.random_delay(1.0, 2.0)
    human.human_type(email_field, config.YOUTUBE_EMAIL)

    next_btn = driver.find_element(By.CSS_SELECTOR, SELECTORS["email_next"])
    human.human_click(next_btn)
    human.random_delay(2.0, 4.0)

    # Password
    password_field = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["password_input"]))
    )
    human.human_type(password_field, config.YOUTUBE_PASSWORD)

    pwd_next = driver.find_element(By.CSS_SELECTOR, SELECTORS["password_next"])
    human.human_click(pwd_next)
    human.random_delay(3.0, 6.0)

    logger.info("Login completado")


def _wait_for_upload_complete(driver, wait_long: WebDriverWait, timeout: int = 300) -> None:
    """
    Espera a que el video termine de subirse y procesarse.

    Args:
        driver: WebDriver
        wait_long: WebDriverWait con timeout largo
        timeout: Segundos máximos a esperar
    """
    logger.info("Esperando que el video se suba...")
    start = time.time()

    while time.time() - start < timeout:
        try:
            # Buscar indicadores de progreso
            progress = driver.find_elements(By.CSS_SELECTOR, "ytcp-video-upload-progress")
            if progress:
                text = progress[0].text.lower()
                if any(word in text for word in ["complete", "completo", "listo", "100%"]):
                    logger.info("Upload completado")
                    return

            # Buscar botón "Siguiente" habilitado (indica que el upload terminó)
            next_btns = driver.find_elements(By.CSS_SELECTOR, "ytcp-button#next-button")
            for btn in next_btns:
                if btn.is_enabled() and not btn.get_attribute("disabled"):
                    logger.info("Botón Siguiente habilitado — upload listo")
                    return

        except Exception:
            pass

        time.sleep(5)
        logger.debug(f"Esperando upload... ({int(time.time() - start)}s)")

    logger.warning(f"Timeout esperando upload ({timeout}s)")


def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str]
) -> bool:
    """
    Sube un video a YouTube Studio usando Selenium con comportamiento humano.

    Flujo:
    1. Abrir YouTube Studio con perfil persistente
    2. Login si no hay sesión (letra por letra)
    3. Click "Crear" → "Subir videos"
    4. Cargar archivo MP4
    5. Completar metadatos (título, descripción, audiencia)
    6. Establecer visibilidad "Público"
    7. Guardar y confirmar

    Args:
        video_path: Path absoluto al archivo MP4
        title: Título del video (max 100 chars)
        description: Descripción del video
        tags: Lista de tags para el video

    Returns:
        True si se subió exitosamente, False si falló

    Raises:
        ValueError: Si las credenciales de YouTube no están configuradas
        FileNotFoundError: Si el archivo de video no existe

    Example:
        >>> success = upload_to_youtube(
        ...     "output/video.mp4",
        ...     "Nunca debí revisar su celular... (Historia real)",
        ...     "En este video...",
        ...     ["#confesion", "#drama", "#historia"]
        ... )
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video no encontrado: {video_path}")

    logger.info(f"Iniciando upload: {video_path.name}")

    for attempt in range(1, config.UPLOAD_MAX_RETRIES + 1):
        driver = None
        try:
            driver = _init_driver()
            wait = WebDriverWait(driver, 30)
            wait_long = WebDriverWait(driver, 120)
            human = HumanBehavior(driver)

            # ── Navegar a YouTube Studio ───────────────────────────────────────
            driver.get(config.YOUTUBE_STUDIO_URL)
            # Pausa larga simulando que la página carga y el usuario la lee
            human.random_delay(5.0, 10.0)
            human.random_scroll()
            human.random_mouse_move()

            # ── Login si es necesario ──────────────────────────────────────────
            if not _is_logged_in(driver, wait):
                logger.info("No hay sesión activa, iniciando login...")
                _login(driver, human, wait)
                driver.get(config.YOUTUBE_STUDIO_URL)
                # Después del login el usuario suele tomarse un momento
                human.random_delay(6.0, 12.0)
                human.random_scroll()

            # ── Exploración previa antes de hacer click en "Crear" ────────────
            # Un humano mira el dashboard antes de ir directo al botón
            human.random_mouse_move()
            human.random_scroll()
            human.random_delay(3.0, 7.0)
            human.random_mouse_move()

            # ── Click "Crear" → "Subir videos" ────────────────────────────────
            logger.info("Haciendo click en 'Crear'...")
            create_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, SELECTORS["create_btn"]))
            )
            human.read_pause()   # el usuario "ve" el botón antes de clickearlo
            human.human_click(create_btn)
            human.random_delay(1.5, 3.5)

            upload_option = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//tp-yt-paper-item[contains(., 'Subir') or contains(., 'Upload')]"))
            )
            human.read_pause()
            human.human_click(upload_option)
            human.random_delay(3.0, 6.0)

            # ── Seleccionar archivo ────────────────────────────────────────────
            logger.info("Seleccionando archivo de video...")
            file_input = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["file_input"]))
            )
            time.sleep(random.uniform(1.5, 3.0))
            file_input.send_keys(str(video_path.absolute()))
            # El upload comienza — el usuario observa mientras sube
            human.random_delay(4.0, 8.0)
            human.random_mouse_move()

            # ── Esperar modal de detalles ──────────────────────────────────────
            title_input = wait_long.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#title-textarea #child-input"))
            )
            # El usuario tarda en leer el modal antes de empezar a editar
            human.read_pause()
            human.random_scroll()
            human.random_delay(2.0, 5.0)

            # ── Título ────────────────────────────────────────────────────────
            logger.info("Escribiendo título...")
            human.human_type(title_input, title)
            # Pausa al terminar de escribir (el usuario revisa lo que escribió)
            human.random_delay(2.0, 5.0)
            human.random_mouse_move()

            # ── Descripción + hashtags ────────────────────────────────────────
            logger.info("Escribiendo descripción...")
            desc_input = driver.find_element(
                By.CSS_SELECTOR, "#description-textarea #child-input"
            )
            hashtags_str = " ".join(
                t if t.startswith("#") else f"#{t}" for t in (tags or [])
            )
            full_description = f"{description}\n\n{hashtags_str}" if hashtags_str else description
            # El usuario tarda más en leer el campo de descripción (es largo)
            human.read_pause()
            human.human_type(desc_input, full_description)
            # Pausa larga después de escribir la descripción (revisión)
            human.random_delay(3.0, 7.0)
            human.random_scroll()
            human.random_delay(2.0, 4.0)

            # ── No es para niños ──────────────────────────────────────────────
            logger.info("Seleccionando 'No es para niños'...")
            try:
                not_kids = wait.until(
                    EC.element_to_be_clickable((
                        By.CSS_SELECTOR,
                        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']"
                    ))
                )
                human.read_pause()
                human.human_click(not_kids)
            except TimeoutException:
                logger.warning("No se encontró botón 'No es para niños', continuando...")

            human.random_delay(2.0, 5.0)
            human.random_mouse_move()

            # ── Siguiente (paso 1 → 2) ─────────────────────────────────────────
            logger.info("Avanzando al paso 2...")
            next_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "ytcp-button#next-button"))
            )
            human.read_pause()   # el usuario revisa todo antes de avanzar
            human.human_click(next_btn)
            human.random_delay(3.0, 7.0)
            human.random_scroll()
            human.random_mouse_move()

            # ── Siguiente (paso 2 → 3: Elementos del video) ───────────────────
            next_btn2 = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "ytcp-button#next-button"))
            )
            human.read_pause()
            human.human_click(next_btn2)
            human.random_delay(3.0, 7.0)
            human.random_mouse_move()

            # ── Siguiente (paso 3 → 4: Verificaciones) ────────────────────────
            next_btn3 = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "ytcp-button#next-button"))
            )
            human.read_pause()
            human.human_click(next_btn3)
            human.random_delay(3.0, 8.0)
            human.random_scroll()
            human.random_mouse_move()

            # ── Visibilidad: Público ───────────────────────────────────────────
            logger.info("Estableciendo visibilidad: Público...")
            public_radio = wait.until(
                EC.element_to_be_clickable((
                    By.CSS_SELECTOR,
                    "tp-yt-paper-radio-button[name='PUBLIC']"
                ))
            )
            human.read_pause()
            human.human_click(public_radio)
            # Pausa larga — es la acción más importante, el usuario duda un momento
            human.random_delay(4.0, 9.0)
            human.random_mouse_move()

            # ── Esperar upload y guardar ───────────────────────────────────────
            _wait_for_upload_complete(driver, wait_long)

            logger.info("Guardando video...")
            save_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "ytcp-button#done-button"))
            )
            # Pausa extra antes de guardar — acción irreversible, el usuario revisa
            human.read_pause()
            human.random_mouse_move()
            human.read_pause()
            human.human_click(save_btn)
            human.random_delay(4.0, 8.0)

            # ── Screenshot de confirmación ─────────────────────────────────────
            screenshot_path = config.LOGS_DIR / f"upload_confirm_{time.strftime('%Y%m%d_%H%M%S')}.png"
            driver.save_screenshot(str(screenshot_path))
            logger.info(f"Screenshot guardado: {screenshot_path.name}")

            logger.info(f"Video subido exitosamente: '{title}'")
            return True

        except Exception as e:
            logger.error(f"Error en upload (intento {attempt}/{config.UPLOAD_MAX_RETRIES}): {e}")

            # Screenshot del error
            if driver:
                try:
                    err_screenshot = config.LOGS_DIR / f"upload_error_{attempt}_{time.strftime('%Y%m%d_%H%M%S')}.png"
                    driver.save_screenshot(str(err_screenshot))
                    logger.info(f"Screenshot de error guardado: {err_screenshot.name}")
                except Exception:
                    pass

            if attempt < config.UPLOAD_MAX_RETRIES:
                logger.info(f"Reintentando en {config.UPLOAD_RETRY_WAIT}s...")
                time.sleep(config.UPLOAD_RETRY_WAIT)

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    logger.error(f"Upload falló tras {config.UPLOAD_MAX_RETRIES} intentos")
    return False
