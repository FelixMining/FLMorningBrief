#!/usr/bin/env python3
"""
FL Morning Brief — Générateur quotidien v2
"""

import json
import os
import logging
import random
from collections import Counter
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
#  Token tracker
# ─────────────────────────────────────────────────────────────────
class TokenTracker:
    PRICES = {
        'claude-haiku-4-5-20251001': (0.80,  4.00),
        'claude-sonnet-4-6':          (3.00, 15.00),
    }

    def __init__(self):
        self.input_tokens  = 0
        self.output_tokens = 0
        self.cost_usd      = 0.0

    def record(self, response, model: str):
        it = getattr(response.usage, 'input_tokens',  0)
        ot = getattr(response.usage, 'output_tokens', 0)
        self.input_tokens  += it
        self.output_tokens += ot
        pi, po = self.PRICES.get(model, (3.0, 15.0))
        self.cost_usd += (it * pi + ot * po) / 1_000_000

    def to_dict(self) -> dict:
        return {
            'tokens_in':  self.input_tokens,
            'tokens_out': self.output_tokens,
            'cost_usd':   round(self.cost_usd, 6),
        }

tracker = TokenTracker()

# ─────────────────────────────────────────────────────────────────
#  Historique & Feedback
# ─────────────────────────────────────────────────────────────────
def load_history(briefs_dir: Path) -> dict:
    h = briefs_dir / 'history.json'
    if h.exists():
        try:
            return json.loads(h.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}

def save_history(briefs_dir: Path, history: dict) -> None:
    (briefs_dir / 'history.json').write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8'
    )

def load_feedback(briefs_dir: Path) -> dict:
    f = briefs_dir / 'feedback.json'
    if f.exists():
        try:
            return json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}

def build_feedback_context(feedback: dict) -> str:
    """Résumé court des préférences utilisateur pour orienter les prompts."""
    liked_domains, disliked_domains, comments = [], [], []
    for date_data in feedback.values():
        if not isinstance(date_data, dict):
            continue
        for fb in date_data.values():
            if not isinstance(fb, dict):
                continue
            if fb.get('like') == 1 and fb.get('domain'):
                liked_domains.append(fb['domain'])
            elif fb.get('like') == -1 and fb.get('domain'):
                disliked_domains.append(fb['domain'])
            if fb.get('comment'):
                comments.append(fb['comment'])
    parts = []
    if liked_domains:
        top = [d for d, _ in Counter(liked_domains).most_common(3)]
        parts.append(f"Domaines appréciés par l'utilisateur : {', '.join(top)}")
    if disliked_domains:
        bot = [d for d, _ in Counter(disliked_domains).most_common(3)]
        parts.append(f"Domaines moins appréciés (réduire légèrement) : {', '.join(bot)}")
    if comments:
        parts.append(f"Commentaires utilisateur récents : {' | '.join(comments[-5:])}")
    return '\n'.join(parts)

# ─────────────────────────────────────────────────────────────────
#  COLLECTEUR — Actualités mondiales
# ─────────────────────────────────────────────────────────────────
def collect_news(cfg: dict) -> list:
    articles = []
    nb       = cfg.get('nb_articles', 3)
    langs    = cfg.get('langue', ['fr', 'en'])

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

    for ticker in cfg.get('indices', []):
        try:
            info  = yf.Ticker(ticker).fast_info
            prev  = float(info.previous_close)
            last  = float(info.last_price)
            chg   = round((last - prev) / prev * 100, 2) if prev else None
            result['indices'].append({
                'ticker': ticker, 'name': _TICKER_NAMES.get(ticker, ticker),
                'price': round(last, 2), 'change_pct': chg,
            })
        except Exception as e:
            log.warning(f'yfinance {ticker}: {e}')
            result['indices'].append({
                'ticker': ticker, 'name': _TICKER_NAMES.get(ticker, ticker),
                'price': None, 'change_pct': None,
            })

    tickers = cfg.get('crypto', [])
    if tickers:
        coin_ids = [_CRYPTO_IDS.get(t, t.lower().replace('-usd', '')) for t in tickers]
        try:
            r = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={'ids': ','.join(coin_ids), 'vs_currencies': 'usd', 'include_24hr_change': 'true'},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            for ticker, cid in zip(tickers, coin_ids):
                if cid in data:
                    result['crypto'].append({
                        'ticker': ticker, 'name': cid.capitalize(),
                        'price': data[cid].get('usd'),
                        'change_pct': round(data[cid].get('usd_24h_change', 0), 2),
                    })
        except Exception as e:
            log.warning(f'CoinGecko: {e}')

    if cfg.get('fear_greed'):
        try:
            r = requests.get('https://api.alternative.me/fng/', params={'limit': 1}, timeout=8)
            r.raise_for_status()
            d = r.json()['data'][0]
            result['fear_greed'] = {'value': int(d['value']), 'label': d['value_classification']}
        except Exception as e:
            log.warning(f'Fear & Greed: {e}')

    return result


# ─────────────────────────────────────────────────────────────────
#  COLLECTEUR — YouTube
# ─────────────────────────────────────────────────────────────────
def collect_youtube(cfg: dict, seen_ids: set = None) -> list:
    if not YOUTUBE_KEY:
        log.warning('YOUTUBE_KEY absent — section YouTube ignorée')
        return []

    chaines      = cfg.get('chaines', [])
    max_age      = cfg.get('max_age_hours', 48)
    fallback_age = cfg.get('fallback_max_age_hours', 168)
    nb           = cfg.get('nb_suggestions', 2)
    now          = datetime.now(ZoneInfo('UTC'))
    seen_ids     = seen_ids or set()

    def get_recent_videos(channel_id: str, hours: int) -> list:
        # uploads playlist = channel ID avec UC → UU (pas d'appel API supplémentaire)
        playlist_id = 'UU' + channel_id[2:]
        cutoff = now - timedelta(hours=hours)
        try:
            r = requests.get(
                'https://www.googleapis.com/youtube/v3/playlistItems',
                params={
                    'key': YOUTUBE_KEY,
                    'playlistId': playlist_id,
                    'part': 'snippet',
                    'maxResults': 5,
                },
                timeout=10,
            )
            r.raise_for_status()
            results = []
            for item in r.json().get('items', []):
                sn     = item['snippet']
                vid_id = sn['resourceId']['videoId']
                pub    = datetime.fromisoformat(sn['publishedAt'].replace('Z', '+00:00'))
                if pub < cutoff or vid_id in seen_ids:
                    continue
                results.append({
                    'title':        sn['title'],
                    'channel':      sn['channelTitle'],
                    'video_id':     vid_id,
                    'url':          f'https://www.youtube.com/watch?v={vid_id}',
                    'thumbnail':    sn['thumbnails'].get('medium', {}).get('url', ''),
                    'published_at': sn['publishedAt'],
                })
            return results
        except Exception as e:
            log.warning(f'YouTube {channel_id}: {e}')
            return []

    pool = []
    for ch in chaines:
        found = get_recent_videos(ch['id'], max_age)
        if not found:
            found = get_recent_videos(ch['id'], fallback_age)
        pool.extend(found)

    random.shuffle(pool)
    return pool[:nb]


# ─────────────────────────────────────────────────────────────────
#  CLAUDE — Résumés & sélection (Haiku)
# ─────────────────────────────────────────────────────────────────
def summarize_articles(
    articles: list, section_label: str, model: str, nb_keep: int,
    feedback_ctx: str = ''
) -> list:
    """Haiku sélectionne les nb_keep meilleurs articles, résume et catégorise.
    Le champ extended_content COMPLÈTE le summary sans le répéter."""
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

    feedback_block = f'\nPréférences utilisateur (orienter légèrement la sélection) :\n{feedback_ctx}\n' if feedback_ctx else ''

    prompt = f"""Section : {section_label} — sélectionne et résume les {nb_keep} articles les plus importants.
{feedback_block}
Articles :
{articles_text}

Réponds UNIQUEMENT avec ce JSON (pas de markdown, pas d'explication) :
{{
  "selected": [
    {{
      "index": 1,
      "title_fr": "Titre en français (traduire si nécessaire, conserver si déjà en français)",
      "summary": "Résumé court en 1-2 phrases percutantes en français. Dense, informatif.",
      "extended_content": "Approfondissement en 5-8 phrases en français. IMPORTANT : ne pas répéter ce qui est dans summary. Apporter du contexte, des chiffres, des causes/conséquences, des acteurs impliqués. Ce paragraphe COMPLÈTE le résumé, il ne le reformule pas.",
      "domain": "Catégorie courte en 2-3 mots max (ex: Conflit Iran, IA & LLM, Politique US, Économie, Faits divers, Open Source, Cybersécurité, Géopolitique)",
      "reading_time": 2
    }}
  ]
}}"""

    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=2500,
            messages=[{'role': 'user', 'content': prompt}],
        )
        tracker.record(resp, model)
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
def generate_culture(
    cfg: dict, model: str,
    past_words: list, past_questions: list
) -> dict:
    """Sonnet génère le mot du jour + QCM interactifs.
    Évite les doublons avec l'historique. Favorise les sciences."""
    empty = {'mot_du_jour': None, 'qcm': []}
    if not claude:
        return empty

    qcm_count  = cfg.get('qcm_count', 3)
    # On oriente fortement vers les sciences
    themes     = cfg.get('qcm_themes', ['sciences', 'physique', 'biologie', 'mathématiques', 'astronomie', 'chimie', 'histoire', 'géographie'])
    mot_active = cfg.get('mot_du_jour', True)

    past_words_str = ', '.join(past_words[-150:]) if past_words else ''
    past_q_str = '\n'.join(f'- {q}' for q in past_questions[-60:]) if past_questions else ''

    no_repeat_words = f'\nMots déjà utilisés (NE PAS RÉPÉTER, choisir un mot différent) : {past_words_str}' if past_words_str else ''
    no_repeat_q = f'\nQuestions déjà posées (NE PAS RÉPÉTER, ne pas poser de question similaire) :\n{past_q_str}' if past_q_str else ''

    prompt = f"""Date : {date.today().isoformat()} — Génère le contenu culturel du brief matinal.
{no_repeat_words}
{no_repeat_q}

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
      "theme": "sciences",
      "question": "Question claire ?",
      "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
      "answer_index": 0,
      "explanation": "Explication courte de la bonne réponse."
    }}
  ]
}}

Règles :
- Mot du jour : terme rare mais beau, pas ésotérique. En français.{'  Inclure.' if mot_active else '  Mettre à null.'}
- QCM : exactement {qcm_count} questions. Thèmes possibles : {', '.join(themes[:6])}.
  IMPORTANT : Au moins 2 questions sur 3 doivent porter sur les sciences (physique, chimie, biologie, maths, astronomie, informatique). Pas plus d'1 question sur l'histoire ou la géographie.
- Tout en français. Réponses correctes variées (pas toujours A).
- Niveau : lycéen avancé / étudiant."""

    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{'role': 'user', 'content': prompt}],
        )
        tracker.record(resp, model)
        text = resp.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('```', 2)[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        log.warning(f'Claude culture: {e}')
        return empty


# ─────────────────────────────────────────────────────────────────
#  CLAUDE — Quiz Actu (Sonnet)
# ─────────────────────────────────────────────────────────────────
def generate_actu_qcm(
    news_articles: list, tech_articles: list, model: str,
    past_questions: list
) -> list:
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

    past_q_str = '\n'.join(f'- {q}' for q in past_questions[-40:]) if past_questions else ''
    no_repeat = f'\nQuestions déjà posées (NE PAS RÉPÉTER) :\n{past_q_str}' if past_q_str else ''

    prompt = f"""Date : {date.today().isoformat()} — Génère 5 questions QCM basées sur les articles d'actualité ci-dessous.
{no_repeat}

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
        tracker.record(resp, model)
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
#  Révision — Questions sur anciens briefs (30 jours glissants)
# ─────────────────────────────────────────────────────────────────
def generate_revision_qcm(history: dict, today_str: str) -> list:
    """Sélectionne 3 questions depuis les briefs des 30 derniers jours."""
    cutoff = (datetime.now() - timedelta(days=30)).date().isoformat()
    pool = []
    for date_key, data in history.items():
        if not isinstance(data, dict):
            continue
        if date_key >= cutoff and date_key != today_str:
            for q in data.get('culture_qcm', []):
                if q.get('question'):
                    pool.append(dict(q, source_date=date_key, review_type='culture'))
            for q in data.get('actu_qcm', []):
                if q.get('question'):
                    pool.append(dict(q, source_date=date_key, review_type='actu'))
    if not pool:
        return []
    return random.sample(pool, min(3, len(pool)))


# ─────────────────────────────────────────────────────────────────
#  Révision — QCM sur les 3 derniers mots du jour (Sonnet)
# ─────────────────────────────────────────────────────────────────
def generate_mot_review_qcm(history: dict, today_str: str, model: str) -> list:
    """Génère des QCM pour tester la mémorisation des 3 derniers mots du jour."""
    dates = sorted([d for d in history.keys() if d != today_str and isinstance(history[d], dict)], reverse=True)[:3]
    mots = []
    for d in dates:
        m = history[d].get('mot_du_jour')
        if m and m.get('mot'):
            mots.append({'date': d, **m})

    if not mots or not claude:
        return []

    mots_text = '\n'.join(
        f'{i+1}. "{m["mot"]}" ({m.get("classe","")}) : {m.get("definition","")}'
        for i, m in enumerate(mots)
    )

    prompt = f"""Génère {len(mots)} questions QCM (une par mot) pour tester la mémorisation des définitions.

Mots à tester :
{mots_text}

Réponds UNIQUEMENT avec ce JSON (tableau) :
[
  {{
    "mot": "le mot testé",
    "question": "Quel est le sens du mot « MOT » ?",
    "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
    "answer_index": 0,
    "explanation": "« MOT » signifie : définition exacte."
  }}
]

Règles :
- Une question par mot, dans l'ordre.
- La bonne réponse doit être à la position answer_index (varier 0, 1, 2, 3).
- Les 3 autres définitions proposées doivent être plausibles mais fausses (mots de sens proche ou inventés).
- Tout en français."""

    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{'role': 'user', 'content': prompt}],
        )
        tracker.record(resp, model)
        text = resp.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('```', 2)[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.strip()
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning(f'Claude mot review QCM: {e}')
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
    log.info('  FL Morning Brief — Génération quotidienne v2')
    log.info('══════════════════════════════════════════')

    cfg      = load_config()
    sec_cfg  = cfg.get('sections', {})
    models   = cfg.get('models', {})
    m_haiku  = models.get('summaries', 'claude-haiku-4-5-20251001')
    m_sonnet = models.get('creative',  'claude-sonnet-4-6')

    tz_paris = ZoneInfo('Europe/Paris')
    today    = datetime.now(tz_paris).date()
    today_str = today.isoformat()

    # ── Chargement contexte historique ───────────────
    briefs_dir = ROOT / 'briefs'
    briefs_dir.mkdir(exist_ok=True)
    history     = load_history(briefs_dir)
    feedback    = load_feedback(briefs_dir)
    feedback_ctx = build_feedback_context(feedback)

    # Extraire mots et questions déjà utilisés
    past_words = [
        v.get('mot_du_jour', {}).get('mot', '')
        for v in history.values()
        if v.get('mot_du_jour')
    ]
    past_words = [w for w in past_words if w]

    past_questions = []
    for v in history.values():
        for q in v.get('culture_qcm', []):
            if q.get('question'):
                past_questions.append(q['question'])
        for q in v.get('actu_qcm', []):
            if q.get('question'):
                past_questions.append(q['question'])

    log.info(f'  Historique : {len(history)} briefs, {len(past_words)} mots, {len(past_questions)} questions')

    gen_start = datetime.now(tz_paris)

    brief = {
        'date':         today_str,
        'date_fr':      format_date_fr(today),
        'generated_at': gen_start.strftime('%H:%M'),
        'sections':     {},
    }

    # ── Actualités ────────────────────────────────────────
    s = sec_cfg.get('news', {})
    if s.get('active', True):
        log.info('[1/5] Actualités mondiales…')
        raw  = collect_news(s)
        nb   = s.get('nb_articles', 3)
        arts = summarize_articles(raw, 'Actualités mondiales', m_haiku, nb, feedback_ctx)
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
        arts = summarize_articles(raw, 'Tech & IA', m_haiku, nb, feedback_ctx)
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
        seen_ids = set(history.get('seen_video_ids', []))
        videos   = collect_youtube(s, seen_ids)
        brief['sections']['youtube'] = {'active': True, 'videos': videos}
        log.info(f'      {len(videos)} vidéos')
        # mémoriser les IDs suggérés pour ne pas les reproposer
        new_seen = list(seen_ids | {v['video_id'] for v in videos})
        history['seen_video_ids'] = new_seen[-500:]  # cap à 500 entrées
    else:
        brief['sections']['youtube'] = {'active': False, 'videos': []}

    # ── Culture ───────────────────────────────────────────
    s = sec_cfg.get('culture', {})
    if s.get('active', True):
        log.info('[5/5] Culture générale (Sonnet)…')
        culture = generate_culture(s, m_sonnet, past_words, past_questions)
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
        actu_qcm = generate_actu_qcm(news_arts, tech_arts, m_sonnet, past_questions)
        log.info(f'    {len(actu_qcm)} questions actu générées')
    else:
        actu_qcm = []
    brief['actu_qcm'] = actu_qcm

    # ── Révision — questions anciens briefs ───────────────
    log.info('[+] Révision (30 jours)…')
    revision_qcm = generate_revision_qcm(history, today_str)
    brief['revision_qcm'] = revision_qcm
    log.info(f'    {len(revision_qcm)} questions de révision')

    # ── QCM mots du jour (3 derniers) ────────────────────
    log.info('[+] QCM mots du jour (Sonnet)…')
    mot_review_qcm = generate_mot_review_qcm(history, today_str, m_sonnet)
    brief['mot_review_qcm'] = mot_review_qcm
    log.info(f'    {len(mot_review_qcm)} QCM mots')

    gen_end   = datetime.now(tz_paris)
    _tok_data = tracker.to_dict()
    brief['generated_at_start'] = gen_start.strftime('%H:%M:%S')
    brief['generated_at_end']   = gen_end.strftime('%H:%M:%S')
    brief['gen_duration']        = int((gen_end - gen_start).total_seconds())
    brief['gen_tokens_in']       = _tok_data['tokens_in']
    brief['gen_tokens_out']      = _tok_data['tokens_out']
    brief['gen_cost']            = _tok_data['cost_usd']

    # ── Rendu HTML ────────────────────────────────────────
    log.info('Rendu Jinja2…')
    env      = Environment(loader=FileSystemLoader(str(ROOT)), autoescape=True)
    template = env.get_template('template.html')
    html     = template.render(brief=brief, config=cfg)

    out = briefs_dir / f'{today_str}.html'
    out.write_text(html, encoding='utf-8')
    log.info(f'Brief sauvegardé → {out}')

    # ── Mise à jour historique ────────────────────────────
    culture_sec = brief['sections'].get('culture', {})
    history[today_str] = {
        'mot_du_jour': culture_sec.get('mot_du_jour'),
        'culture_qcm': culture_sec.get('qcm', []),
        'actu_qcm':    brief.get('actu_qcm', []),
    }
    save_history(briefs_dir, history)
    log.info('Historique mis à jour.')

    # ── Méta-données de génération ────────────────────────
    meta = {
        'date':               today_str,
        'generated_at_start': brief['generated_at_start'],
        'generated_at_end':   brief['generated_at_end'],
        'duration_seconds':   brief['gen_duration'],
        'tokens_in':          brief['gen_tokens_in'],
        'tokens_out':         brief['gen_tokens_out'],
        'cost_usd':           brief['gen_cost'],
    }
    (briefs_dir / 'latest_meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    log.info(f'Méta : {_tok_data["tokens_in"]}→{_tok_data["tokens_out"]} tokens, ${_tok_data["cost_usd"]:.4f}')

    update_index(briefs_dir, cfg)
    log.info('Index mis à jour.')
    log.info('══ Terminé ══')


if __name__ == '__main__':
    main()
