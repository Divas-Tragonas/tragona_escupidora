# 🐉 Tragona Escupidora — Generador de Recaps D&D

Sistema automático que convierte un VOD de Twitch en un recap estructurado de tu sesión de D&D, publicado en GitHub Pages.

```
VOD de Twitch → descarga audio → transcripción Groq Whisper → recap Claude → GitHub Pages
```

---

## Cómo funciona

1. Pulsas **Nueva Sesión** en la web y pegas la URL del VOD de Twitch.
2. La web llama a la API de GitHub (con tu Personal Access Token) para disparar el workflow.
3. GitHub Actions descarga el audio, lo transcribe con Groq Whisper large-v3 y genera el recap con Claude claude-opus-4-7.
4. El workflow hace commit del Markdown y actualiza el índice. GitHub Pages lo publica automáticamente.

El proceso completo tarda entre **10 y 30 minutos** dependiendo de la duración del VOD.

---

## Requisitos previos

- Cuenta en **GitHub**
- API key de **Groq** → [console.groq.com](https://console.groq.com)
- API key de **Anthropic** → [console.anthropic.com](https://console.anthropic.com)

---

## Configuración paso a paso

### 1. Crear el repositorio en GitHub

```bash
# Opción A: desde la CLI de GitHub
gh repo create TragonaEscupidora --public --source=. --push

# Opción B: manualmente
# 1. Ve a github.com → New repository
# 2. Nombre: TragonaEscupidora (o el que prefieras)
# 3. Visibilidad: Public (necesario para GitHub Pages gratuito)
# 4. Sin README ni .gitignore (ya los tienes)
# 5. Copia la URL del repo y ejecuta:
git init
git add .
git commit -m "Primer commit: estructura base"
git remote add origin https://github.com/TU_USUARIO/TragonaEscupidora.git
git branch -M main
git push -u origin main
```

> ⚠️ **Importante:** el workflow `generate-recap.yml` debe estar en la rama `main` antes de poder dispararlo vía API.

---

### 2. Configurar los Secrets de GitHub Actions

Ve a tu repositorio → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Añade estos dos secrets:

| Nombre | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | Tu API key de Anthropic (empieza por `sk-ant-...`) |
| `GROQ_API_KEY` | Tu API key de Groq (empieza por `gsk_...`) |

---

### 3. Activar GitHub Pages

1. Ve a tu repositorio → **Settings** → **Pages**
2. En **Source** selecciona: `Deploy from a branch`
3. En **Branch** selecciona: `main` y carpeta `/docs`
4. Pulsa **Save**

En unos segundos tu web estará disponible en:
```
https://TU_USUARIO.github.io/TragonaEscupidora/
```

---

### 4. Generar el Personal Access Token (PAT)

El frontend necesita un token para disparar el workflow vía la API de GitHub.

1. Ve a **github.com** → tu avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
2. Pulsa **Generate new token (classic)**
3. Nombre: `dnd-recap-trigger` (o el que quieras)
4. Expiración: 90 días (o la que prefieras)
5. Scopes: marca únicamente **`workflow`**
6. Pulsa **Generate token** y copia el token (`ghp_...`)

> 🔒 El token se guarda en el `localStorage` de tu navegador. Nunca sale de tu máquina salvo para llamar a la API de GitHub.

---

### 5. Editar la configuración

Edita el archivo `docs/config.json` con tus datos:

```json
{
  "github_owner": "TU_USUARIO_GITHUB",
  "github_repo": "TragonaEscupidora",
  "workflow_id": "generate-recap.yml",
  "titulo_campana": "Tragona Escupidora",
  "descripcion": "Recaps automáticos de nuestras sesiones de D&D"
}
```

Haz commit y push del cambio:

```bash
git add docs/config.json
git commit -m "Configurar usuario y repo"
git push
```

---

### 6. Probar el flujo completo

1. Abre tu web en `https://TU_USUARIO.github.io/TragonaEscupidora/`
2. Pulsa **Nueva Sesión**
3. Pega la URL de un VOD de Twitch (p.ej. `https://www.twitch.tv/videos/1234567890`)
4. Añade el token en el campo PAT y pulsa **Guardar**
5. Pulsa **Generar Recap**
6. Verás el mensaje `✓ Workflow disparado`
7. Ve a tu repo en GitHub → pestaña **Actions** para ver el progreso
8. Cuando termine (~15 min), refresca la web. Aparecerá el recap.

---

## Estructura del proyecto

```
TragonaEscupidora/
├── .github/
│   └── workflows/
│       └── generate-recap.yml    # Workflow: descarga → transcribe → recap → commit
├── docs/                         # Raíz de GitHub Pages
│   ├── index.html                # Web con tema D&D
│   ├── config.json               # ← EDITA ESTO con tu usuario/repo
│   ├── recaps.json               # Índice de recaps (actualizado por el workflow)
│   └── recaps/
│       └── YYYY-MM-DD-titulo.md  # Recaps generados
├── scripts/
│   ├── generate_recap.py         # Script principal Python
│   └── requirements.txt
├── .gitignore
└── README.md
```

---

## Secciones del recap

Cada recap generado incluye:

| Sección | Contenido |
|---|---|
| **Resumen Ejecutivo** | 3-5 frases con lo más importante |
| **⚔️ Combates** | Encuentros, enemigos, resultado |
| **👥 NPCs Encontrados** | Personajes con descripción y rol |
| **💰 Loot y Recompensas** | Todo el botín conseguido |
| **🎯 Decisiones Clave** | Decisiones del grupo y consecuencias |
| **🪝 Plot Hooks** | Hilos argumentales abiertos |
| **✨ Momentos Memorables** | Los mejores momentos de la sesión |
| **💬 Quotes Destacadas** | Citas memorables atribuidas |

---

## Ejecutar el script localmente (opcional)

Si quieres probar el script en tu máquina:

```bash
# Instalar dependencias del sistema
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Ubuntu

# Instalar dependencias Python
cd scripts
pip install -r requirements.txt

# Ejecutar
export ANTHROPIC_API_KEY="sk-ant-..."
export GROQ_API_KEY="gsk_..."

python generate_recap.py \
  --url "https://www.twitch.tv/videos/1234567890" \
  --titulo "Sesión 1: El Comienzo" \
  --salida ../docs/recaps
```

---

## Solución de problemas

### El workflow falla con "Error: ANTHROPIC_API_KEY no está configurado"
→ Comprueba que has añadido el secret correctamente en Settings → Secrets.

### yt-dlp no puede descargar el VOD
→ Los VODs de suscriptores o muy antiguos (más de 60 días) pueden no estar disponibles. Prueba primero en tu navegador si el VOD es accesible públicamente.

### El botón "Generar Recap" da error 401
→ El PAT no es válido o no tiene el scope `workflow`. Genera uno nuevo.

### El botón "Generar Recap" da error 404
→ `config.json` tiene un `github_owner` o `github_repo` incorrecto. También puede ser que el workflow no esté en la rama `main` todavía.

### El recap tarda más de 30 minutos
→ Es normal para sesiones de 4-5 horas. GitHub Actions tiene un límite de 6 horas. Si supera ese tiempo, divide el VOD en partes.

### GitHub Actions no tiene permisos para hacer push
→ Ve a Settings → Actions → General → Workflow permissions → selecciona **Read and write permissions**.

---

## Costes aproximados

| Servicio | Coste para una sesión de 3h |
|---|---|
| Groq Whisper large-v3 | ~$0.05 |
| Anthropic claude-opus-4-7 | ~$0.20-0.50 |
| GitHub Actions | Gratuito (plan free) |
| GitHub Pages | Gratuito |

**Total por sesión: ~$0.25-0.55**

---

## Personalización

- **Cambiar el idioma por defecto:** edita el valor `default: "es"` en el workflow YAML.
- **Cambiar la duración de los segmentos:** añade `--duracion-seg 300` (5 min) al comando del workflow para mejor granularidad.
- **Cambiar el modelo de Claude:** edita la constante `model="claude-opus-4-7"` en `generate_recap.py`.
- **Añadir secciones al recap:** edita el `PROMPT_RECAP` en `generate_recap.py`.
- **Cambiar el nombre de la campaña:** edita `titulo_campana` en `docs/config.json`.
