# notify-me

`notify-me` is a small, dependency-free Python monitor for property rental listings.
It checks configured search result pages, remembers listing URLs it has already
seen, and notifies you when new rental listings appear.

It is designed for free or low-cost use:

- run it on your laptop with cron or Task Scheduler
- run it continuously on a small home server/Raspberry Pi
- run it on GitHub Actions on a schedule
- notify through free Telegram, Discord, or generic webhook messages

> Note: PropertyGuru, iProperty, Mudah.my, and similar sites may change their
> HTML, block automated requests, or restrict scraping in their terms. Use
> reasonable intervals, keep the configured delay, and prefer official alerts or
> feeds where a site offers them.

## How it works

1. You create normal rental searches in your browser on PropertyGuru,
   iProperty, Mudah.my, or another listing site.
2. Copy each search result URL into `config.json`.
3. Configure a regex that matches real listing URLs for that site.
4. Run the monitor periodically.
5. The first run records existing listings without notifying by default.
6. Later runs notify only for listing URLs that were not seen before.

## Quick start

Requires Python 3.10 or newer.

```bash
cp config.example.json config.json
```

Edit `config.json`:

- set `"enabled": true` for the sources you want
- replace each example `url` with your exact search result URL
- adjust `listing_url_patterns` if the site URL shape changes
- enable one notification provider

Test what the monitor can detect:

```bash
python -m notify_me --config config.json --list-current
```

Record the current listings without external notifications:

```bash
python -m notify_me --config config.json
```

Run continuously every 15 minutes on your own machine:

```bash
python -m notify_me --config config.json --interval-seconds 900
```

## Notifications

Console output is enabled by default. For phone notifications, Telegram is the
simplest free option.

### Telegram

1. Message `@BotFather` in Telegram and create a bot.
2. Save the bot token as `TELEGRAM_BOT_TOKEN`.
3. Get your chat ID and save it as `TELEGRAM_CHAT_ID`.
4. In `config.json`, set:

```json
"telegram": {
  "enabled": true,
  "bot_token_env": "TELEGRAM_BOT_TOKEN",
  "chat_id_env": "TELEGRAM_CHAT_ID"
}
```

Then run:

```bash
export TELEGRAM_BOT_TOKEN="123456:your-token"
export TELEGRAM_CHAT_ID="123456789"
python -m notify_me --config config.json
```

### Discord

Create a Discord channel webhook, save it as `DISCORD_WEBHOOK_URL`, and enable
the Discord provider in `config.json`.

### Generic webhook

Set `NOTIFY_WEBHOOK_URL` and enable the `webhook` provider. The monitor posts:

```json
{
  "text": "message",
  "listings": []
}
```

## Free scheduled run with GitHub Actions

This repository includes `.github/workflows/property-monitor.yml`, which runs
every 30 minutes and can also be triggered manually.

To use it:

1. Copy `config.example.json` to `config.json`.
2. Configure your enabled sources and listing URL patterns.
3. Commit `config.json` to your repository. Do not put tokens in it.
4. Add notification tokens as GitHub repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `DISCORD_WEBHOOK_URL`
   - `NOTIFY_WEBHOOK_URL`
5. Enable GitHub Actions for the repository.

The workflow stores `state/listings.json` in the GitHub Actions cache so it can
compare new runs against previously seen listings.

## Config reference

```json
{
  "state_file": "state/listings.json",
  "notify_on_first_run": false,
  "max_new_per_source": 10,
  "max_seen_per_source": 5000,
  "request": {
    "timeout_seconds": 20,
    "delay_seconds": 3,
    "max_response_bytes": 5000000,
    "user_agent": "notify-me-property-monitor/0.1 personal rental search"
  },
  "notifications": {
    "console": { "enabled": true },
    "telegram": { "enabled": false },
    "discord": { "enabled": false },
    "webhook": { "enabled": false }
  },
  "sources": [
    {
      "name": "PropertyGuru Malaysia saved search",
      "enabled": true,
      "url": "https://www.propertyguru.com.my/property-for-rent?...",
      "listing_url_patterns": [
        "propertyguru\\.com\\.my/property-listing/"
      ],
      "exclude_url_patterns": []
    }
  ]
}
```

### Adding similar services

Add another object to `sources`:

```json
{
  "name": "My rental site",
  "enabled": true,
  "url": "https://example.com/rentals?area=petaling-jaya",
  "listing_url_patterns": [
    "example\\.com/.*/rent"
  ],
  "exclude_url_patterns": []
}
```

Use `--list-current` to check whether your regex finds actual listing pages.

## Development

Run the tests:

```bash
python -m unittest discover
```
