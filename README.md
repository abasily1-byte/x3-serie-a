# X3 Serie A Dashboard

An automatically updated, newspaper-style Italian Serie A scoreboard for the
528 × 792 pixel XTEINK X3 e-reader and CrossPoint Reader.

![Serie A dashboard preview](SerieA.bmp)

[Download the latest SerieA.bmp](https://raw.githubusercontent.com/abasily1-byte/x3-serie-a/main/SerieA.bmp)

## What it shows

- Current Serie A season and giornata (matchweek)
- Completed-match count and final scores
- Live matches when present
- Upcoming fixtures
- Kickoff and update times in Pacific Time

The image is an uncompressed 24-bit RGB BMP containing only pure black and pure
white pixels for reliable XTEINK X3 display.

## How the pieces work together

- **GitHub** refreshes the Serie A data and generates `SerieA.bmp` automatically.
- **Tasker** on an Android phone downloads it as `Download/sleep.bmp` and sends
  it to the X3.
- **CrossPoint Reader** receives the file and uses `/sleep.bmp` as the custom
  sleep screen.

GitHub keeps the name `SerieA.bmp`. Tasker gives the phone's downloaded copy the
name `sleep.bmp`, so no repository file needs to be renamed.

## Data sources

Version 1 uses two unattended, no-key sources:

- [ESPN](https://www.espn.com/soccer/league/_/name/ita.1) supplies Serie A
  fixtures, scores, match status, team names, and kickoff times.
- [FixtureDownload](https://fixturedownload.com/) supplies the explicit round
  number used for the giornata label and schedule mapping.

No API key or GitHub secret is required.

## Automatic updates

The [GitHub Actions workflow](.github/workflows/update.yml):

- runs automatically at minute 29 of every hour (UTC);
- can also be started manually with **Run workflow**;
- prevents concurrent runs on the same branch;
- validates the image before committing it; and
- commits `SerieA.bmp` only when the generated file changed.

GitHub schedules can occasionally be delayed during busy periods.

## Set up the X3 sleep screen

### 1. Prepare CrossPoint Reader

On the X3:

1. Place or use `sleep.bmp` in the root of the SD card.
2. Select the **Custom sleep-screen** option in CrossPoint Reader.
3. Open **File Transfer → Join Network** whenever you want to refresh it.

The File Transfer screen displays the X3's IP address. Replace `<X3-IP>` below
with that numeric address. The phone and X3 must be on the same local network.

### 2. Create the Tasker task

Add these three **HTTP Request** actions in this order.

#### Action 1 — Download the latest dashboard

- **Method:** `GET`
- **URL:**

  ```text
  https://raw.githubusercontent.com/abasily1-byte/x3-serie-a/main/SerieA.bmp
  ```

- **File To Save With Output:**

  ```text
  Download/sleep.bmp
  ```

#### Action 2 — Delete the old X3 sleep screen

- **Method:** `POST`
- **URL:**

  ```text
  http://<X3-IP>/delete
  ```

- **Body:**

  ```text
  path=/sleep.bmp
  ```

#### Action 3 — Upload the new sleep screen

- **Method:** `POST`
- **URL:**

  ```text
  http://<X3-IP>/upload?path=/
  ```

- **Body:**

  ```text
  x=1
  ```

- **File To Send:**

  ```text
  file:Download/sleep.bmp
  ```

### 3. Refresh the display

1. On the X3, open **File Transfer → Join Network**.
2. Run the Tasker task and wait a few seconds.
3. Exit File Transfer.
4. Put the X3 to sleep.

The newly uploaded `sleep.bmp` should appear without re-selecting the custom
sleep-screen image each time.

### Android `.local` hostname note

`http://crosspoint.local` works on some phones, but Android may fail to resolve
the `.local` hostname. If Tasker reports an `UnknownHostException`, replace
`crosspoint.local` with the numeric IP shown by **File Transfer → Join Network**.

You can optionally reserve the X3's IP address in your router's DHCP settings
to make the address more stable.

## Optional NFC shortcut

Create a Tasker NFC profile with:

**Tasker → Profiles → + → Event → Net → NFC Tag**

Scan a tag and associate it with the Serie A update task. The everyday workflow
then becomes:

**X3 → File Transfer → Join Network → tap phone on NFC tag → wait a few seconds
→ exit File Transfer → sleep**

## Run locally

```bash
python -m pip install -r requirements.txt
python generate_serie_a_bmp.py --validate
```

To validate an existing image without fetching data:

```bash
python generate_serie_a_bmp.py --validate-only
```

The repository is public, so anyone may clone, fork, or adapt it for another
league or device.
