import http.server
import json
import os
import urllib.parse
import tempfile
import shutil
import time
import socketserver
import subprocess
import sys

# Instala ffmpeg se não estiver disponível
def garantir_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        print("[ffmpeg] já instalado")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[ffmpeg] instalando...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "imageio[ffmpeg]"])
        # Tenta via apt como fallback
        try:
            subprocess.run(["apt-get", "install", "-y", "-q", "ffmpeg"], capture_output=True)
            print("[ffmpeg] instalado via apt")
        except Exception:
            pass

garantir_ffmpeg()

import yt_dlp

PORT = int(os.environ.get("PORT", 8765))

PASTA_TEMP = os.path.join(tempfile.gettempdir(), "videohub_downloads")
os.makedirs(PASTA_TEMP, exist_ok=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Salva cookies das variáveis de ambiente em arquivos temporários
COOKIES_DIR = os.path.join(tempfile.gettempdir(), "videohub_cookies")
os.makedirs(COOKIES_DIR, exist_ok=True)

COOKIE_FILES = {}
variacoes = {
    "youtube": ["COOKIES_YOUTUBE", "Cookies_youtube", "cookies_youtube"],
    "instagram": ["COOKIES_INSTAGRAM", "Cookies_instagram", "cookies_instagram"],
}
for plat, nomes in variacoes.items():
    for env_var in nomes:
        conteudo = os.environ.get(env_var, "")
        if conteudo.strip():
            caminho = os.path.join(COOKIES_DIR, f"{plat}.txt")
            with open(caminho, "w", encoding="utf-8") as f:
                f.write(conteudo)
            COOKIE_FILES[plat] = caminho
            print(f"[cookies] {plat} carregado via {env_var}")
            break
    else:
        print(f"[cookies] AVISO: nenhum cookie encontrado para {plat}")

def get_cookie_file(url):
    if "youtube.com" in url or "youtu.be" in url:
        return COOKIE_FILES.get("youtube")
    if "instagram.com" in url:
        return COOKIE_FILES.get("instagram")
    return None

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        # Serve o HTML principal
        if parsed.path == "/" or parsed.path == "/index.html":
            self.servir_arquivo("videohub.html", "text/html; charset=utf-8")
            return

        # Busca formatos do vídeo
        if parsed.path == "/info":
            url = params.get("url", [None])[0]
            if not url:
                self.responder_json(400, {"erro": "URL nao fornecida"})
                return
            try:
                ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
                cookie_file = get_cookie_file(url)
                if cookie_file:
                    ydl_opts["cookiefile"] = cookie_file
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)

                # Monta opções de qualidade simples e confiáveis
                formatos = [
                    {"label": "Melhor qualidade (MP4)", "format_id": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best", "ext": "mp4"},
                    {"label": "Qualidade média (MP4)",  "format_id": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best", "ext": "mp4"},
                    {"label": "Menor tamanho (MP4)",    "format_id": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best", "ext": "mp4"},
                ]

                self.responder_json(200, {
                    "titulo": info.get("title", "video"),
                    "thumbnail": info.get("thumbnail", ""),
                    "formatos": formatos[:5],
                })
            except Exception as e:
                self.responder_json(500, {"erro": str(e)})

        # Faz o download e envia o arquivo
        elif parsed.path == "/baixar":
            url = params.get("url", [None])[0]
            fmt = params.get("fmt", [None])[0]

            if not url or not fmt:
                self.responder_json(400, {"erro": "Parametros faltando"})
                return

            try:
                nome_base = f"video_{int(time.time())}"
                saida = os.path.join(PASTA_TEMP, nome_base + ".%(ext)s")
                saida_final = os.path.join(PASTA_TEMP, nome_base + ".mp4")

                cookie_file = get_cookie_file(url)

                # Sempre força MP4 com H.264+AAC (compatível com iOS)
                formatos_tentativa = [
                    fmt,
                    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "best",
                ]

                sucesso = False
                ultimo_erro = ""
                for formato_tentativa in formatos_tentativa:
                    ydl_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "format": formato_tentativa,
                        "outtmpl": saida,
                        "noplaylist": True,
                        "merge_output_format": "mp4",
                        # Converte para H.264+AAC — único codec que iOS aceita sem erro
                        "postprocessors": [{
                            "key": "FFmpegVideoConvertor",
                            "preferedformat": "mp4",
                        }],
                        "postprocessor_args": {
                            "ffmpegvideoconvertor": [
                                "-vcodec", "libx264",
                                "-acodec", "aac",
                                "-movflags", "+faststart",
                                "-pix_fmt", "yuv420p",
                            ]
                        },
                    }
                    if cookie_file:
                        ydl_opts["cookiefile"] = cookie_file
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([url])
                        sucesso = True
                        break
                    except Exception as e:
                        ultimo_erro = str(e)
                        continue

                if not sucesso:
                    self.responder_json(500, {"erro": ultimo_erro})
                    return

                # Encontra o arquivo gerado
                arquivo = None
                for f in os.listdir(PASTA_TEMP):
                    if f.startswith(nome_base):
                        arquivo = os.path.join(PASTA_TEMP, f)
                        break

                if not arquivo or not os.path.exists(arquivo):
                    self.responder_json(500, {"erro": "Arquivo nao encontrado apos download"})
                    return

                tamanho = os.path.getsize(arquivo)
                self.send_response(200)
                self.send_cors()
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Disposition", 'attachment; filename="video.mp4"')
                self.send_header("Content-Length", str(tamanho))
                self.end_headers()

                with open(arquivo, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)

                os.remove(arquivo)

            except Exception as e:
                self.responder_json(500, {"erro": str(e)})

        else:
            self.responder_json(404, {"erro": "Rota nao encontrada"})

    def servir_arquivo(self, nome, content_type):
        caminho = os.path.join(BASE_DIR, nome)
        if not os.path.exists(caminho):
            self.responder_json(404, {"erro": "Arquivo nao encontrado"})
            return
        with open(caminho, "rb") as f:
            conteudo = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(conteudo)))
        self.end_headers()
        self.wfile.write(conteudo)

    def responder_json(self, codigo, dados):
        body = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ServidorMultiThread(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass


if __name__ == "__main__":
    print(f"VideoHub rodando na porta {PORT}")
    while True:
        try:
            server = ServidorMultiThread(("0.0.0.0", PORT), Handler)
            server.serve_forever()
        except Exception as e:
            print(f"[aviso] Reiniciando: {e}")
            time.sleep(1)
