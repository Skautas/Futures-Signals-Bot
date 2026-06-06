# Audio meditacijos (MP3)

Į šį aplanką įkelkite failus **tiksliais vardais** (mažosios raidės, brūkšniai kaip žemiau), kad veiktų grotuvai puslapyje **4 lygis**.

## Įrašai 01–06 (UCLA Mindful, anglų kalba)

Visi šeši failai šiame repozitorijoje yra nekeisti MP3 iš
[UCLA Mindful — Guided meditations](https://www.uclahealth.org/uclamindful/guided-meditations).
**Licencija:** [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/) —
nekomercinis naudojimas, be išvestinių kūrinių, su autoriaus nurodymu. Žr.
**[ATTRIBUTION.txt](ATTRIBUTION.txt)** (originalūs URL ir žemėlapis).

| Failas šiame projekte | UCLA šaltinio failas (santrauka) |
|----------------------|----------------------------------|
| `01-kvepavimas-ir-kunas.mp3` | `01_Breathing_Meditation.mp3` |
| `02-ryto-ramybe.mp3` | `Body-Sound-Meditation.mp3` |
| `03-kuno-skanavimas.mp3` | `Body-Scan-Meditation.mp3` |
| `04-vakaro-uzbaigimas.mp3` | `Body-Scan-for-Sleep.mp3` |
| `05-demesys-grazinimas.mp3` | `02_Breath_Sound_Body_Meditation.mp3` |
| `06-ilgasis-atsipalaidavimas.mp3` | `03_Complete_Meditation_Instructions.mp3` (~19 min.) |

**Pastaba:** `06-ilgasis-atsipalaidavimas.mp3` pavadinimas istoriniu atžvilgiu siejasi su
„ilguoju atsipalaidavimu“ svetainėje; turinyje tai UCLA **pilnos meditacijos
instrukcijos** anglų kalba. Lietuviški tekstai skaitymui: visų šešių įrašų puslapis
`meditacijos/lietuviski-tekstai-prie-bibliotekos-audio.html` ir atskiras ilgas
scenarijus `meditacijos/ilgasis-atsipalaidavimas.html`.

## Techniniai patarimai

- Formatas: **MP3**.
- Jei turite tik **MP4** (pvz. OBS), prieš įkeliant konvertuokite į MP3:
  `python scripts/mp4_to_mp3.py kelias/į.mp4 kelias/iš.mp3` (reikia
  `pip install imageio-ffmpeg`).
- Jei keičiate pavadinimus, atnaujinkite ir `index.html` `<source src="...">` eilutes.

## Autorystė ir teisės

- **01–06:** laikytis UCLA CC BY-NC-ND sąlygų; žr. `ATTRIBUTION.txt`.
- Lietuviškas tekstas puslapyje — atskiras turinys pagal jūsų teises jį platinti.
