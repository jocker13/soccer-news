import io
import os
from datetime import datetime, timezone
from time import mktime

import feedparser
import requests
import tweepy
from dotenv import load_dotenv
from flask import Flask, jsonify, request, render_template

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
X_API_KEY = os.environ.get("X_API_KEY")
X_API_SECRET = os.environ.get("X_API_SECRET")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
X_ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET")

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

RSS_FEEDS = [
    ("BBC Sport Football", "http://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("Sky Sports Football", "https://www.skysports.com/rss/12040"),
    ("ESPN FC", "https://www.espn.com/espn/rss/soccer/news"),
    ("Goal.com", "https://www.goal.com/feeds/en/news"),
    ("The Guardian Football", "https://www.theguardian.com/football/rss"),
]

app = Flask(__name__)


def get_x_client():
    return tweepy.Client(
        consumer_key=X_API_KEY,
        consumer_secret=X_API_SECRET,
        access_token=X_ACCESS_TOKEN,
        access_token_secret=X_ACCESS_SECRET,
    )


def get_x_api_v1():
    auth = tweepy.OAuth1UserHandler(
        X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
    )
    return tweepy.API(auth)


def extract_image(entry):
    media = entry.get("media_content") or entry.get("media_thumbnail")
    if media:
        return media[0].get("url")
    for enc in entry.get("enclosures", []):
        if str(enc.get("type", "")).startswith("image"):
            return enc.get("href") or enc.get("url")
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/news")
def api_news():
    query = request.args.get("q", "").strip().lower()

    articles = []
    for source_name, feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception:
            continue
        for entry in parsed.entries:
            title = entry.get("title", "")
            description = entry.get("summary", "")
            if query and query not in title.lower() and query not in description.lower():
                continue
            published = entry.get("published_parsed")
            published_dt = (
                datetime.fromtimestamp(mktime(published), tz=timezone.utc)
                if published
                else None
            )
            articles.append(
                {
                    "title": title,
                    "description": description,
                    "url": entry.get("link"),
                    "image": extract_image(entry),
                    "source": source_name,
                    "published": published_dt.isoformat() if published_dt else None,
                }
            )

    articles.sort(key=lambda a: a["published"] or "", reverse=True)
    return jsonify({"articles": articles[:20]})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    body = request.get_json(force=True)
    title = body.get("title", "")
    description = body.get("description", "")
    url = body.get("url", "")

    if not GROQ_API_KEY:
        return jsonify({"error": "GROQ_API_KEY belum diset di .env"}), 500

    prompt = (
        "Buat satu draft tweet bahasa Indonesia dari berita bola ini. "
        "Gaya bahasa santai, gaul, kayak anak muda ngobrol di Twitter (boleh bahasa "
        "sehari-hari/slang, jangan kaku kayak berita formal). Lempar statement atau "
        "pertanyaan yang mancing orang buat berdebat/komentar (contoh: adu pendapat soal "
        "keputusan pelatih, performa pemain, siapa yang salah). Maksimal 200 karakter, "
        "hashtag maks 2 relevan (klub/liga), data/nama/angka tetap akurat sesuai judul & "
        "ringkasan. Jangan sertakan link/URL apapun (link asli ditambahkan terpisah). "
        "Output HANYA teks tweet itu sendiri, tanpa tanda kutip, tanpa catatan jumlah "
        "karakter, tanpa penjelasan tambahan.\n\n"
        f"Judul: {title}\nRingkasan: {description}"
    )

    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "reasoning_effort": "low",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
            "max_tokens": 400,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        return jsonify({"error": f"Groq error: {resp.text}"}), 502

    draft = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
    tweet = f"{draft}\n{url}" if url else draft
    tweet = tweet[:280]
    return jsonify({"tweet": tweet, "image": body.get("image")})


@app.route("/api/post", methods=["POST"])
def api_post():
    body = request.get_json(force=True)
    text = body.get("text", "").strip()
    image_url = body.get("image")
    if not text:
        return jsonify({"error": "teks tweet kosong"}), 400
    if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET]):
        return jsonify({"error": "kredensial X belum lengkap di .env"}), 500

    try:
        media_ids = None
        if image_url:
            img_resp = requests.get(image_url, timeout=15)
            img_resp.raise_for_status()
            filename = image_url.split("/")[-1].split("?")[0] or "image.jpg"
            api_v1 = get_x_api_v1()
            media = api_v1.media_upload(
                filename=filename, file=io.BytesIO(img_resp.content)
            )
            media_ids = [media.media_id]

        client = get_x_client()
        result = client.create_tweet(text=text, media_ids=media_ids)
        tweet_id = result.data["id"]
        return jsonify({"ok": True, "tweet_id": tweet_id})
    except tweepy.TweepyException as e:
        return jsonify({"error": str(e)}), 502
    except requests.RequestException as e:
        return jsonify({"error": f"gagal download gambar: {e}"}), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", debug=True, port=port)
