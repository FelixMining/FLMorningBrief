#!/usr/bin/env python3
"""
FL Morning Brief — Générateur quotidien
Lit config.json → collecte APIs → Claude API → Jinja2 → briefs/YYYY-MM-DD.html
"""

import json
import os
import sys
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from anthropic import Anthropic
from jinja2 import Environment, FileSystemLoader

# ─────────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
#  Chemins & config
# ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent

def load_config() -> dict:
    with open(ROOT / 'config.json', encoding='utf-8') as f:
        return json.load(f)

# ─────────────────────────────────────────────────────────────────
#  Secrets (injectés par GitHub Actions)
# ─────────────────────────────────────────────────────────────────
NEWSAPI_KEY   = os.environ.get('NEWSAPI_KEY',   '')
GUARDIAN_KEY  = os.environ.get('GUARDIAN_KEY',  '')
YOUTUBE_KEY   = os.environ.get('YOUTUBE_KEY',   '')
ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

claude = Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

# ─────────────────────────────────────────────────────────────────
#  COLLECTEUR — Actualités mondiales
# ─────────────────────────────────────────────────────────────────
def collect_news(cfg: dict) -> list:
    articles = []
    nb       = cfg.get('nb_articles', 3)
    langs    = cfg.get('langue', ['fr', 'en'])

    # NewsAPI — top-headlines
    if NEWSAPI_KEY:
        for lang in langs[:2]:
            try:
                r = requests.get(
                    'https://newsapi.org/v2/top-headlines',
                    params={
                        'apiKey': NEWSAPI_KEY,
                        'language': lang,
                        'pageSize': nb * 3,
                        'category': 'general',
                    },
                    timeout=10,
                )
                r.raise_for_status()
                for a in r.json().get('articles', []):
                    title = a.get('title', '')
                    if title and '[Removed]' not in title and a.get('url'):
                        articles.append({
                            'title':        title,
                            'source':       a.get('source', {}).get('name', ''),
                            'url':          a['url'],
                            'content':      a.get('description') or a.get('content') or '',
                            'published_at': a.get('publishedAt', ''),
                            'image_url':    a.get('urlToImage') or '',
                        })
            except Exception as e:
                log.warning(f'NewsAPI [{lang}]: {e}')

    # The Guardian — fallback / complément
    if GUARDIAN_KEY and len(articles) < nb * 2:
        try:
            r = requests.get(
                'https://content.guardianapis.com/search',
                params={
                    'api-key':    GUARDIAN_KEY,
                    'page-size':  nb * 2,
                    'show-fields': 'trailText,headline,thumbnail',
                    'order-by':   'newest',
                },
                timeout=10,
            )
            r.raise_for_status()
            for a in r.json().get('response', {}).get('results', []):
                title = a.get('fields', {}).get('headline') or a.get('webTitle', '')
                if title:
                    articles.append({
                        'title':        title,
                        'source':       'The Guardian',
                        'url':          a.get('webUrl', ''),
                        'content':      a.get('fields', {}).get('trailText', ''),
                        'published_at': a.get('webPublicationDate', ''),
                        'image_url':    a.get('fields', {}).get('thumbnail') or '',
                    })
        except Exception as e:
            log.warning(f'Guardian API: {e}')

    # Dédoublonnage sur l'URL
    seen, unique = set(), []
    for a in articles:
        if a['url'] not in seen:
            seen.add(a['url'])
            unique.append(a)

    return unique


# ─────────────────────────────────────────────────────────────────
#  COLLECTEUR — Tech & IA
# ─────────────────────────────────────────────────────────────────
def collect_tech(cfg: dict) -> list:
    articles = []
    nb     = cfg.get('nb_articles', 5)
    sujets = cfg.get('sujets', ['IA générative', 'LLM', 'hardware'])
    query  = ' OR '.join(f'"{s}"' for s in sujets[:4])

    # NewsAPI — everything (tech)
    if NEWSAPI_KEY:
        try:
            r = requests.get(
                'https://newsapi.org/v2/everything',
                params={
                    'apiKey':   NEWSAPI_KEY,
                    'q':        query,
                    'language': 'en',
                    'sortBy':   'publishedAt',
                    'pageSize': nb * 3,
                },
                timeout=10,
            )
            r.raise_for_status()
            for a in r.json().get('articles', []):
                title = a.get('title', '')
                if title and '[Removed]' not in title and a.get('url'):
                    articles.append({
                        'title':        title,
                        'source':       a.get('source', {}).get('name', ''),
                        'url':          a['url'],
                        'content':      a.get('description') or '',
                        'published_at': a.get('publishedAt', ''),
                        'image_url':    a.get('urlToImage') or '',
                    })
        except Exception as e:
            log.warning(f'NewsAPI tech: {e}')

    # Hacker News — sans clé, toujours disponible
    keywords = [s.lower() for s in sujets] + ['ai', 'llm', 'gpt', 'claude', 'mistral', 'model', 'openai', 'anthropic']
    try:
        top_ids = requests.get(
            'https://hacker-news.firebaseio.com/v0/topstories.json', timeout=8
        ).json()[:30]
        for item_id in top_ids:
            try:
                item = requests.get(
                    f'https://hacker-news.firebaseio.com/v0/item/{item_id}.json', timeout=5
                ).json()
                if not item or item.get('type') != 'story' or not item.get('url'):
                    continue
                title = item.get('title', '').lower()
                if any(kw in title for kw in keywords):
                    articles.append({
                        'title':        item['title'],
                        'source':       'Hacker News',
                        'url':          item['url'],
                        'content':      '',
                        'published_at': '',
                        'image_url':    '',
                    })
            except Exception:
                pass
    except Exception as e:
        log.warning(f'Hacker News: {e}')

    # Dédoublonnage
    seen, unique = set(), []
    for a in articles:
        if a['url'] not in seen:
            seen.add(a['url'])
            unique.append(a)

    return unique


# ─────────────────────────────────────────────────────────────────
#  COLLECTEUR — Finance & Marchés
# ─────────────────────────────────────────────────────────────────
_TICKER_NAMES = {
    '^FCHI': 'CAC 40',   '^GSPC': 'S&P 500', '^NDX': 'Nasdaq 100',
    '^DJI':  'Dow Jones', '^FTSE': 'FTSE 100', '^GDAXI': 'DAX',
}
_CRYPTO_IDS = {
    'BTC-USD': 'bitcoin', 'ETH-USD': 'ethereum',
    'SOL-USD': 'solana',  'BNB-USD': 'binancecoin', 'XRP-USD': 'ripple',
}

def collect_finance(cfg: dict) -> dict:
    result = {'indices': [], 'crypto': [], 'fear_greed': None, 'macro_events': []}

    # Indices boursiers via yfinance
    for ticker in cfg.get('indices', []):
        try:
            info = yf.Ticker(ticker).fast_info
            prev  = float(info.previous_close)
            last  = float(info.last_price)
            chg   = round((last - prev) / prev * 100, 2) if prev else None
            result['indices'].append({
                'ticker':     ticker,
                'name':       _TICKER_NAMES.get(ticker, ticker),
                'price':      round(last, 2),
                'change_pct': chg,
            })
        except Exception as e:
            log.warning(f'yfinance {ticker}: {e}')
            result['indices'].append({
                'ticker': ticker, 'name': _TICKER_NAMES.get(ticker, ticker),
                'price': None, 'change_pct': None,
            })

    # Crypto via CoinGecko (gratuit, sans clé)
    tickers = cfg.get('crypto', [])
    if tickers:
        coin_ids = [_CRYPTO_IDS.get(t, t.lower().replace('-usd', '')) for t in tickers]
        try:
            r = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={
                    'ids':              ','.join(coin_ids),
                    'vs_currencies':    'usd',
                    'include_24hr_change': 'true',
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            for ticker, cid in zip(tickers, coin_ids):
                if cid in data:
                    result['crypto'].append({
                        'ticker':     ticker,
                        'name':       cid.capitalize(),
                        'price':      data[cid].get('usd'),
                        'change_pct': round(data[cid].get('usd_24h_change', 0), 2),
                    })
        except Exception as e:
            log.warning(f'CoinGecko: {e}')

    # Fear & Greed Index (alternative.me)
    if cfg.get('fear_greed'):
        try:
            r = requests.get(
                'https://api.alternative.me/fng/', params={'limit': 1}, timeout=8
            )
            r.raise_for_status()
            d = r.json()['data'][0]
            result['fear_greed'] = {
                'value': int(d['value']),
                'label': d['value_classification'],
            }
        except Exception as e:
            log.warning(f'Fear & Greed: {e}')

    return result


# ─────────────────────────────────────────────────────────────────
#  COLLECTEUR — YouTube
# ─────────────────────────────────────────────────────────────────
def collect_youtube(cfg: dict) -> list:
    if not YOUTUBE_KEY:
        log.warning('YOUTUBE_KEY absent — section YouTube ignorée')
        return []

    chaines     = cfg.get('chaines', [])
    max_age     = cfg.get('max_age_hours', 24)
    fallback_age= cfg.get('fallback_max_age_hours', 168)
    nb          = cfg.get('nb_suggestions', 2)
    now         = datetime.now(ZoneInfo('UTC'))

    def search_channel(channel_id: str, hours: int) -> list:
        after = (now - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
        try:
            r = requests.get(
                'https://www.googleapis.com/youtube/v3/search',
                params={
                    'key':           YOUTUBE_KEY,
                    'channelId':     channel_id,
                    'part':          'snippet',
                    'order':         'date',
                    'publishedAfter': after,
                    'maxResults':    3,
                    'type':          'video',
                },
                timeout=10,
            )
            r.raise_for_status()
            return [
                {
                    'title':      item['snippet']['title'],
                    'channel':    item['snippet']['channelTitle'],
                    'video_id':   item['id']['videoId'],
                    'url':        f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                    'thumbnail':  item['snippet']['thumbnails'].get('medium', {}).get('url', ''),
                    'published_at': item['snippet']['publishedAt'],
                }
                for item in r.json().get('items', [])
            ]
        except Exception as e:
            log.warning(f'YouTube {channel_id}: {e}')
            return []

    videos = []
    for ch in chaines:
        found = search_channel(ch['id'], max_age)
        if not found:
            found = search_channel(ch['id'], fallback_age)
        videos.extend(found)
        if len(videos) >= nb * 2:
            break

    return videos[:nb]


# ─────────────────────────────────────────────────────────────────
#  CLAUDE — Résumés & sélection (Haiku)
# ─────────────────────────────────────────────────────────────────
def summarize_articles(articles: list, section_label: str, model: str, nb_keep: int) -> list:
    """Haiku sélectionne les nb_keep meilleurs articles, traduit les titres, résume et catégorise."""
    if not articles:
        return []
    if not claude:
        log.warning('ANTHROPIC_API_KEY absent — résumés désactivés')
        return [dict(a, summary=a.get('content', '')[:200], reading_time=2,
                     title_fr=a.get('title', ''), domain='', extended_content='') for a in articles[:nb_keep]]

    articles_text = '\n'.join(
        f'{i+1}. [{a.get("source","")}] {a["title"]}\n   {a.get("content","")[:400]}'
        for i, a in enumerate(articles[:20])
    )

    prompt = f"""Section : {section_label} — sélectionne et résume les {nb_keep} articles les plus importants.

Articles :
{articles_text}

Réponds UNIQUEMENT avec ce JSON (pas de markdown, pas d'explication) :
{{
  "selected": [
    {{
      "index": 1,
      "title_fr": "Titre en français (traduire si nécessaire, conserver si déjà en français)",
      "summary": "Résumé court en 1-2 phrases en français.",
      "extended_content": "Résumé étendu en 4-5 phrases en français avec contexte et détails supplémentaires.",
      "domain": "Catégorie courte en 2-3 mots max (ex: Conflit Iran, IA & LLM, Politique US, Économie, Faits divers, Open Source, Cybersécurité, Géopolitique)",
      "reading_time": 2
    }}
  ]
}}"""

    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = resp.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('```', 2)[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.strip()
        data = json.loads(text)
        result = []
        for sel in data.get('selected', []):
            idx = sel['index'] - 1
            if 0 <= idx < len(articles):
                a = dict(articles[idx])
                a['summary']          = sel.get('summary', '')
                a['title_fr']         = sel.get('title_fr', a.get('title', ''))
                a['domain']           = sel.get('domain', '')
                a['extended_content'] = sel.get('extended_content', '')
                a['reading_time']     = sel.get('reading_time', 2)
                result.append(a)
        return result
    except Exception as e:
        log.warning(f'Claude summarize ({section_label}): {e}')
        return [dict(a, summary=a.get('content', '')[:200], reading_time=2,
                     title_fr=a.get('title', ''), domain='', extended_content='') for a in articles[:nb_keep]]


# ─────────────────────────────────────────────────────────────────
#  CLAUDE — Culture générale (Sonnet)
# ─────────────────────────────────────────────────────────────────
def generate_culture(cfg: dict, model: str) -> dict:
    """Sonnet génère le mot du jour + QCM interactifs."""
    empty = {'mot_du_jour': None, 'qcm': []}
    if not claude:
        return empty

    qcm_count  = cfg.get('qcm_count', 3)
    themes     = cfg.get('qcm_themes', ['histoire', 'sciences', 'géographie'])
    mot_active = cfg.get('mot_du_jour', True)

    prompt = f"""Date : {date.today().isoformat()} — Génère le contenu culturel du brief matinal.

Réponds UNIQUEMENT avec ce JSON (pas de markdown) :
{{
  "mot_du_jour": {{
    "mot": "...",
    "classe": "nom masculin / verbe / adjectif / ...",
    "definition": "Définition claire en 1-2 phrases.",
    "etymologie": "Origine en 1 phrase.",
    "exemple": "Phrase d'exemple élégante utilisant le mot."
  }},
  "qcm": [
    {{
      "theme": "histoire",
      "question": "Question claire ?",
      "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
      "answer_index": 0,
      "explanation": "Explication courte de la bonne réponse."
    }}
  ]
}}

Règles :
- Mot du jour : terme rare mais beau, pas ésotérique. En français.{'  Inclure.' if mot_active else '  Mettre à null.'}
- QCM : exactement {qcm_count} questions. Thèmes parmi : {', '.join(themes)}. Variés, niveau moyen.
- Tout en français. Réponses correctes variées (pas toujours A)."""

    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = resp.content[0].text.strip()
        # Nettoie les blocs markdown si présents
        if text.startswith('```'):
            text = text.split('```', 2)[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        log.warning(f'Claude culture: {e}')
        return empty


# ─────────────────────────────────────────────────────────────────
#  CLAUDE — Quiz Actu (Sonnet) — 5 QCM sur les articles du jour
# ─────────────────────────────────────────────────────────────────
def generate_actu_qcm(news_articles: list, tech_articles: list, model: str) -> list:
    """Sonnet génère 5 QCM basés sur les articles d'actualité sélectionnés du jour."""
    if not claude:
        return []
    all_articles = news_articles + tech_articles
    if not all_articles:
        return []

    articles_text = '\n'.join(
        f'{i+1}. [{a.get("source","")}] {a.get("title_fr", a.get("title",""))}\n'
        f'   {a.get("extended_content", a.get("summary",""))[:350]}'
        for i, a in enumerate(all_articles[:10])
    )

    prompt = f"""Date : {date.today().isoformat()} — Génère 5 questions QCM basées sur les articles d'actualité ci-dessous.

Articles :
{articles_text}

Réponds UNIQUEMENT avec ce JSON (tableau, pas de markdown) :
[
  {{
    "theme": "Mot-clé court de l'article source (2-3 mots max)",
    "question": "Question précise sur un fait concret mentionné dans l'article ?",
    "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
    "answer_index": 0,
    "explanation": "Explication courte de la bonne réponse avec contexte."
  }}
]

Règles :
- Exactement 5 questions, une par article si possible.
- Tester des faits précis (chiffre, nom propre, événement, pays, date).
- Les 4 options doivent être plausibles, une seule est correcte.
- Varier les bons indices (pas toujours 0).
- Tout en français."""

    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = resp.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('```', 2)[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.strip()
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning(f'Claude actu QCM: {e}')
        return []


# ─────────────────────────────────────────────────────────────────
#  Utilitaires
# ─────────────────────────────────────────────────────────────────
_JOURS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
_MOIS  = ['janvier','février','mars','avril','mai','juin',
           'juillet','août','septembre','octobre','novembre','décembre']

def format_date_fr(d: date) -> str:
    return f"{_JOURS[d.weekday()]} {d.day} {_MOIS[d.month - 1]} {d.year}"


def update_index(briefs_dir: Path, cfg: dict) -> None:
    """Génère briefs/index.html — liste de tous les briefs archivés."""
    files = sorted(briefs_dir.glob('20*.html'), reverse=True)
    links = '\n'.join(
        f'<li><a href="{f.name}">{f.stem}</a></li>'
        for f in files if f.name != 'index.html'
    )
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FL Morning Brief — Archive</title>
<style>
  body{{font-family:system-ui;background:#080810;color:#dde1f0;padding:32px 20px;max-width:400px;margin:0 auto}}
  h1{{font-size:18px;margin-bottom:24px;color:#818cf8}}
  ul{{list-style:none;padding:0;display:flex;flex-direction:column;gap:10px}}
  a{{color:#818cf8;text-decoration:none;font-family:monospace;font-size:14px}}
  a:hover{{color:#a5b4fc}}
</style>
</head>
<body>
<h1>FL Morning Brief — Archive</h1>
<ul>{links}</ul>
</body>
</html>"""
    (briefs_dir / 'index.html').write_text(html, encoding='utf-8')


# ─────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────
def main() -> None:
    log.info('══════════════════════════════════════════')
    log.info('  FL Morning Brief — Génération quotidienne')
    log.info('══════════════════════════════════════════')

    cfg      = load_config()
    sec_cfg  = cfg.get('sections', {})
    models   = cfg.get('models', {})
    m_haiku  = models.get('summaries', 'claude-haiku-4-5-20251001')
    m_sonnet = models.get('creative',  'claude-sonnet-4-6')

    today = datetime.now(ZoneInfo('Europe/Paris')).date()
    brief = {
        'date':         today.isoformat(),
        'date_fr':      format_date_fr(today),
        'generated_at': datetime.now(ZoneInfo('UTC')).strftime('%H:%M UTC'),
        'sections':     {},
    }

    # ── Actualités ────────────────────────────────────────
    s = sec_cfg.get('news', {})
    if s.get('active', True):
        log.info('[1/5] Actualités mondiales…')
        raw  = collect_news(s)
        nb   = s.get('nb_articles', 3)
        arts = summarize_articles(raw, 'Actualités mondiales', m_haiku, nb)
        brief['sections']['news'] = {'active': True, 'articles': arts}
        log.info(f'      {len(arts)} articles sélectionnés')
    else:
        brief['sections']['news'] = {'active': False, 'articles': []}

    # ── Tech & IA ─────────────────────────────────────────
    s = sec_cfg.get('tech', {})
    if s.get('active', True):
        log.info('[2/5] Tech & IA…')
        raw  = collect_tech(s)
        nb   = s.get('nb_articles', 5)
        arts = summarize_articles(raw, 'Tech & IA', m_haiku, nb)
        brief['sections']['tech'] = {'active': True, 'articles': arts}
        log.info(f'      {len(arts)} articles sélectionnés')
    else:
        brief['sections']['tech'] = {'active': False, 'articles': []}

    # ── Finance ───────────────────────────────────────────
    s = sec_cfg.get('finance', {})
    if s.get('active', True):
        log.info('[3/5] Finance & Marchés…')
        fin = collect_finance(s)
        brief['sections']['finance'] = {'active': True, **fin}
        log.info(f'      {len(fin["indices"])} indices, {len(fin["crypto"])} crypto')
    else:
        brief['sections']['finance'] = {'active': False, 'indices': [], 'crypto': [], 'fear_greed': None}

    # ── YouTube ───────────────────────────────────────────
    s = sec_cfg.get('youtube', {})
    if s.get('active', True):
        log.info('[4/5] YouTube…')
        videos = collect_youtube(s)
        brief['sections']['youtube'] = {'active': True, 'videos': videos}
        log.info(f'      {len(videos)} vidéos')
    else:
        brief['sections']['youtube'] = {'active': False, 'videos': []}

    # ── Culture ───────────────────────────────────────────
    s = sec_cfg.get('culture', {})
    if s.get('active', True):
        log.info('[5/5] Culture générale (Sonnet)…')
        culture = generate_culture(s, m_sonnet)
        brief['sections']['culture'] = {'active': True, **culture}
        qcm_n = len(culture.get('qcm', []))
        log.info(f'      Mot du jour + {qcm_n} QCM générés')
    else:
        brief['sections']['culture'] = {'active': False}

    # ── Quiz Actu ─────────────────────────────────────────
    news_arts = brief['sections']['news'].get('articles', [])
    tech_arts = brief['sections']['tech'].get('articles', [])
    if news_arts or tech_arts:
        log.info('[+] Quiz Actu (Sonnet)…')
        actu_qcm = generate_actu_qcm(news_arts, tech_arts, m_sonnet)
        log.info(f'    {len(actu_qcm)} questions actu générées')
    else:
        actu_qcm = []
    brief['actu_qcm'] = actu_qcm

    # ── Rendu HTML ────────────────────────────────────────
    log.info('Rendu Jinja2…')
    env      = Environment(loader=FileSystemLoader(str(ROOT)), autoescape=True)
    template = env.get_template('template.html')
    html     = template.render(brief=brief, config=cfg)

    briefs_dir = ROOT / 'briefs'
    briefs_dir.mkdir(exist_ok=True)
    out = briefs_dir / f'{today.isoformat()}.html'
    out.write_text(html, encoding='utf-8')
    log.info(f'Brief sauvegardé → {out}')

    update_index(briefs_dir, cfg)
    log.info('Index mis à jour.')
    log.info('══ Terminé ══')


if __name__ == '__main__':
    main()
