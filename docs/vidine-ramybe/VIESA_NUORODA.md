# Vieša nuoroda (GitHub Pages)

## Kaip veikia dabar

Darbo eiga **Deploy Vidinė ramybė (gh-pages)** nukopijuoja `docs/vidine-ramybe/` į atskirą šaką **`gh-pages`**. Tam **nereikia** „GitHub Actions“ kaip Pages šaltinio ir **nebūna** `Get Pages site failed`.

### 1. Palaukite žalios varnelės Actions

https://github.com/Skautas/Futures-Signals-Bot/actions  

Jei workflow dar nebuvo paleistas po paskutinio push — **Actions** → **Deploy Vidinė ramybė (gh-pages)** → **Run workflow**.

### 2. Vieną kartą: įjunkite Pages iš šakos

1. https://github.com/Skautas/Futures-Signals-Bot/settings/pages  
2. **Build and deployment** → **Source**: **Deploy from a branch**  
3. **Branch**: `gh-pages`, aplankas **/ (root)**  
4. **Save**

### 3. Atidarykite svetainę

**https://skautas.github.io/Futures-Signals-Bot/**

---

## Atnaujinimai

Keičiate `vidines_ramybes_mokykla/` → sinchronizuokite į `docs/vidine-ramybe/`, `git push` — workflow vėl atnaujins `gh-pages`.

## Kita vieta be GitHub

**Netlify Drop:** https://app.netlify.com/drop
