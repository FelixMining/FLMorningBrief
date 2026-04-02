# FLMorningBrief — Cahier de réflexion

> Démarré le 2026-03-27. Système autonome de génération d'un brief matinal quotidien.
> Statut : **réflexion / conception** — rien d'implémenté.

---

## Concept

Chaque matin à ~6h (pendant que je dors), un système automatisé :
1. Collecte des données via APIs
2. Génère le contenu via Claude API
3. Publie une page HTML complète accessible depuis mon téléphone

Plage de consultation : **6h35 → 7h10** (petit-déjeuner), environ **35 minutes**.

---

## Structure de la page (cible)

| Section | Durée | Contenu |
|---------|-------|---------|
| Actualités mondiales | ~5 min | Résumés d'articles sur l'actualité globale |
| Tech / Finance | ~10 min | Événements IA/Tech, cours financiers, actu macro-éco |
| Culture générale | ~3 min | Mot rare du dictionnaire, QCM interactif |
| Suggestion vidéo | ~20 min | YouTube prioritaire + fallback autres chaînes |

---

## Architecture technique retenue (à valider)

```
[6h00] GitHub Actions cron
    ↓
[Python script] Collecte données via APIs
    ↓
[Claude API] Génère un JSON structuré (contenu pur, pas de HTML)
    ↓
[Jinja2] Injecte le JSON dans template.html fixe
    ↓
[GitHub Pages] Publie la page → accessible depuis le téléphone
```

**Principe clé :** Claude ne génère jamais de HTML — seulement du JSON. Le template est fixe et versionné. Économise ~70-80% de tokens + résultat plus fiable.

---

## Sources de données envisagées

### Actualités
- **NewsAPI.org** — free tier 100 req/jour, sources internationales
- **The Guardian API** — gratuit, qualité éditoriale
- **GNews** — alternative FR-friendly
- **RSS génériques** — en fallback

### Tech / IA
- NewsAPI filtré `technology`
- **Hacker News API** — public, sans clé
- **arXiv RSS** — derniers papiers IA importants

### Finance / Marchés
- **yfinance** (Python) — Yahoo Finance, gratuit
- **CoinGecko API** — crypto, gratuit, complet
- **Fear & Greed Index** (CNN) — API publique, 1 chiffre
- **TradingView embed** — widget iframe dans le HTML, cours live sans API
- **Calendrier macro-économique** — Investing.com RSS (décisions Fed, CPI, rapport emploi US…)
- Alpha Vantage (free tier limité) — en option

### YouTube
- **YouTube Data API v3** — quota 10 000 unités/jour (largement suffisant)
- Logique : chercher par ID de chaîne les vidéos <24h → fallback <7j → fallback autres chaînes tech/finance

**Chaînes prioritaires :**
1. Micode + Underscore_ (deux chaînes distinctes)
2. Benjamin Code + chaîne secondaire
3. Defend Intelligence
4. V2F + Underflow

Si aucune vidéo récente de ces chaînes → suggestions algo pertinentes tech/finance.

### Culture générale
- **Mot du dictionnaire** : Claude génère directement (pas d'API externe nécessaire)
- **QCM** : Claude génère 3-5 questions HTML+JS interactives

---

## Modèles Claude utilisés

| Tâche | Modèle |
|-------|--------|
| Résumés d'articles | Haiku |
| Tri / sélection des news | Haiku |
| Sélection YouTube | Haiku |
| **QCM + mot du jour** | **Sonnet** (créativité + qualité pédagogique) |

Coût estimé : **< $0.005 / jour**.

---

## Fonctionnalités UX envisagées

### Core
- Design mobile-first, dark mode par défaut
- Archive navigable : chaque jour = `/briefs/2026-03-27.html` + index des dates
- Progress bar de lecture par section
- Temps de lecture estimé par article
- "Marquer comme lu" (JS localStorage)

### Enrichissements potentiels
- **Audio (Edge TTS)** : génère un MP3 en même temps → bouton "Écouter le résumé" pour consommer sans regarder l'écran. Gratuit, voix française excellente.
- **Timer discret** par section (compte à rebours, toggle)
- **Feedback loop** : bouton "utile / inutile" par section → `feedback.json` → le script adapte les proportions après 1 semaine
- **GitHub Trending** — top repos du jour, intégrable en section bonus
- **Product Hunt** — top lancements de la veille (API publique)
- **PWA** : service worker → disponible offline + installable sur écran d'accueil

---

## Planification / Déploiement

- **Scheduler** : GitHub Actions cron (pas besoin que le PC soit allumé)
- **Heure** : `0 5 * * *` UTC → 6h00 Paris hiver / `0 4 * * *` → 6h00 Paris été
- Alternative : gérer les deux avec une variable d'env ou accepter le décalage d'1h en été
- **Hébergement** : GitHub Pages (gratuit, zero infra)
- **Config** : fichier `config.yaml` versionné → sujets, chaînes YT, actifs financiers, langue

---

## Ce qui reste à définir

- [ ] **Actifs financiers spécifiques** à suivre (actions, crypto, indices ?)
- [ ] **Sujets tech prioritaires** (ex : toujours IA générative ? Hardware ? Startups ?)
- [ ] **Langue des contenus sources** : anglais uniquement, FR aussi, ou les deux ?
- [ ] **Heure cible exacte** du brief : 6h00 pile ou marge de sécurité à 5h30 ?
- [ ] **Fuseau horaire** : gérer heure d'été/hiver ou heure fixe UTC ?
- [ ] **Nombre d'articles** par section (3 ? 5 ? selon la durée cible)
- [ ] **Fallback** si une API est down (contenu partiel ou page vide ?)
- [ ] **Nom de domaine** custom ou sous-domaine GitHub Pages suffit ?
- [ ] **Audio activé par défaut** ou bouton manuel ?
- [ ] **Feedback loop** : implémenté dès le début ou en v2 ?

---

## Décisions prises

- 2026-03-27 — Template HTML fixe + JSON généré par Claude → Jinja2 injecte (pas de HTML généré par IA)
- 2026-03-27 — GitHub Actions comme scheduler (pas de script local)
- 2026-03-27 — Hybride Haiku/Sonnet selon la tâche
- 2026-03-27 — GitHub Pages comme hébergement

---

## Prochaines étapes (quand on attaque)

1. Définir les réponses aux points "Ce qui reste à définir" ci-dessus
2. Concevoir le template HTML (design + slots Jinja2)
3. Mettre en place le repo GitHub + GitHub Actions
4. Script Python : collecte des données section par section
5. Intégration Claude API (JSON structuré)
6. Tests en local → validation du rendu
7. Déploiement GitHub Pages
8. Activer le cron et surveiller les premières générations
