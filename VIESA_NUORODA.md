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

**QR puslapis (svetainė arba Telegram):**  
https://skautas.github.io/Futures-Signals-Bot/qr-vidine-ramybe.html

**Nušvitimo kelias (struktūra ir nuorodos):**  
https://skautas.github.io/Futures-Signals-Bot/kelias.html

### Jei vis tiek 404

- Dar kartą patikrinkite **Settings → Pages**: šaltinis turi būti **Deploy from a branch** → **`gh-pages`** → **`/ (root)`** (ne `main`, ne `/docs`).
- Palaukite **iki 10 minučių** po „Save“.
- Jei repozitorija **privati** — GitHub Pages gali būti nepasiekiamas nemokamai; žr. repozitorijos šaknyje **`PAGES_SETUP.md`**.

---

## Atnaujinimai

Keičiate `vidines_ramybes_mokykla/` → sinchronizuokite į `docs/vidine-ramybe/`, tada **į commitą įtraukite visus failus**, kuriuos turi matyti svetainė (įskaitant MP3):

- Jei `docs/vidine-ramybe/audio/` ar `meditacijos/` yra tik lokaliai (**ne** `git add`), GitHub Pages juose **nebūs** — telefone ir kompiuteryje grotuvai liks tušti arba nuorodos neveiks.
- Po `git add` + `git commit` + `git push` į `main` darbo eiga vėl įkels visą `docs/vidine-ramybe/` į `gh-pages`.
- Jei **telefone vis dar senas tekstas ar stiliai** po push: į `index.html` (ir kitus HTML) įrašyti naujesnį `?v=...` prie `styles.css` ir `app.js` nuorodų, vėl commit + push; arba telefone atidaryti svetainę **privačiame lange** / išvalyti talpyklą.

## Kita vieta be GitHub

**Netlify Drop:** https://app.netlify.com/drop
