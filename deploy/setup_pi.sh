#!/bin/bash
# setup_pi.sh — Instalar Crypto Shorts Factory en Raspberry Pi
# Ejecutar como: bash setup_pi.sh
# Requiere: Raspberry Pi OS 64-bit, Pi 4 con 4GB+ RAM recomendado

set -e
INSTALL_DIR="/home/pi/crypto_shorts_factory"
SERVICE_NAME="crypto-shorts"

echo "=== Crypto Shorts Factory — Setup Raspberry Pi ==="

# 1. Dependencias del sistema
echo "[1/6] Instalando dependencias del sistema..."
sudo apt update -qq
sudo apt install -y \
    python3 python3-pip python3-venv \
    chromium-browser \
    ffmpeg \
    xvfb \
    git

# 2. Clonar/copiar el proyecto (ajusta la URL si usas otro repo)
echo "[2/6] Configurando directorio del proyecto..."
if [ ! -d "$INSTALL_DIR" ]; then
    echo "  Clona tu repo aquí: git clone <tu_repo> $INSTALL_DIR"
    echo "  O copia los archivos manualmente a $INSTALL_DIR"
    echo "  Luego vuelve a ejecutar este script."
    exit 1
fi
cd "$INSTALL_DIR"

# 3. Entorno virtual Python
echo "[3/6] Creando entorno virtual..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 4. Ollama (LLM local, opcional — si usas solo Groq puedes saltarlo)
echo "[4/6] Instalando Ollama (LLM local)..."
if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.ai/install.sh | sh
    # Descargar modelo por defecto
    ollama pull llama3.2:3b &
fi

# 5. Configurar DISPLAY para Chrome sin pantalla física
echo "[5/6] Configurando pantalla virtual Xvfb..."
# Iniciamos Xvfb al boot via systemd también (ver crypto-shorts.service)
export DISPLAY=:99

# 6. Instalar servicio systemd
echo "[6/6] Instalando servicio systemd..."
sudo cp "$INSTALL_DIR/deploy/crypto-shorts.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "=== Setup completado ==="
echo ""
echo "PRÓXIMOS PASOS (importantes, hacer en orden):"
echo ""
echo "1. Copia tu .env al servidor:"
echo "   scp .env pi@<IP_PI>:$INSTALL_DIR/.env"
echo ""
echo "2. Inicia sesión en YouTube UNA VEZ con pantalla física o VNC:"
echo "   export DISPLAY=:99 && Xvfb :99 -screen 0 1920x1080x24 &"
echo "   DISPLAY=:99 chromium-browser studio.youtube.com"
echo "   → Inicia sesión → cierra Chrome → las cookies quedan guardadas"
echo ""
echo "3. Arranca el servicio:"
echo "   sudo systemctl start $SERVICE_NAME"
echo ""
echo "4. Ver logs en tiempo real:"
echo "   journalctl -u $SERVICE_NAME -f"
echo ""
echo "5. Para 2 videos/día (en lugar de 3), añade al .env:"
echo "   VIDEOS_PER_DAY=2"
