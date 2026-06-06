# GitHub Pages — kodėl svetainė neatsidaro po sėkmingo Actions

Darbo eiga **Deploy Vidinė ramybė (gh-pages)** tik nukopijuoja failus į šaką **`gh-pages`**.  
**Tikroji svetainė** atsiranda tik tada, kai GitHub žino, kad iš tos šakos reikia **rodyti puslapį**.

## Patikrinkite (būtina)

1. Eikite į: **Settings → Pages** (šis repozitorija).
2. **Build and deployment** → **Source** turi būti **Deploy from a branch** (ne „None“, ne tik „GitHub Actions“ jei nenaudojate to šaltinio).
3. Po šaką pasirinkite **`gh-pages`**, aplankas **`/ (root)`**.
4. **Save**.
5. Palaukite kelias minutes ir atidarykite:

   **https://skautas.github.io/Futures-Signals-Bot/**

## Dažnos klaidos

| Požymis | Tikėtina priežastis |
|--------|----------------------|
| 404 „There isn't a GitHub Pages site“ | Pages išjungta arba Source = **None**. |
| 404 po teisingo nustatymo | Palaukite iki **10 min**; hard refresh (Ctrl+F5). |
| Vis dar 404 | Šaka vis dar **main** arba aplankas **/docs** — turi būti **gh-pages** + **/** (root). |
| Repo **private** | Nemokamame plane Pages gali neveikti — darykite repo **public** arba naudokite Netlify. |

## Alternatyva be GitHub Pages

Įkelkite aplanką `docs/vidine-ramybe/` į [Netlify Drop](https://app.netlify.com/drop) — gausite `*.netlify.app` nuorodą iškart.
