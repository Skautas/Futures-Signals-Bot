# QR programa (Vidinės Ramybės Mokykla)

## Kas tai

Failas **`qr-vidine-ramybe.html`** — paprastas puslapis naršyklėje, kuris sugeneruoja **QR kodą**
iš jūsų įvesto URL. Numatyta nuoroda veda į viešą svetainę (4 lygis / meditacijos); galite
įrašyti **Telegram boto** adresą, pvz. `https://t.me/JusuBotas`.

Nuoroda įsimenama šiame įrenginyje (`localStorage`), kol ją pakeisite ar išvalysite svetainės
duomenis naršyklėje.

## Viešas adresas

Jei svetainė talpinama GitHub Pages (žr. `docs/vidine-ramybe/VIESA_NUORODA.md`), QR puslapis bus:

`https://<jūsų-github-pages>/qr-vidine-ramybe.html`

## Telegram botas

1. Sukurkite botą per [BotFather](https://t.me/BotFather), gaukite `t.me/...` vardą.
2. Atidarykite QR puslapį, į URL lauką įrašykite `https://t.me/<botas>`.
3. Paspauskite **Atnaujinti QR kodą** — parodykite ar atsispausdinkite ekraną dalyviams.

Techniškai **„botas“** čia tiesiog nuoroda: QR atidaro Telegram; atskiras serverinis botas
šiam puslapiui nereikalingas.

## Techninė pastaba

QR generuojamas naršyklėje per [qrcode](https://www.npmjs.com/package/qrcode) (CDN). Reikia
interneto pirmam įkrovimui.
