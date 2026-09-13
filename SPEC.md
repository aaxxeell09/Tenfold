# Tenfold : spécification complète du projet

CoreWeave Hacks, Agent Loops Hackathon, 12 et 13 septembre 2026, 400 Alabama Street, San Francisco.
Ce document est la source de vérité pour les deux coéquipiers et pour leurs instances Claude Code. Tout ce qui n'est pas ici n'est pas dans le périmètre. Il vit dans le repo sous `SPEC.md`, référencé par `CLAUDE.md`. Les amendements de la section 19 sont intégrés dans le corps du texte ; en cas de doute la section 19 fait foi. Le worktree du critic ne contient pas ce fichier.

---

## 1. Ce que nous construisons

**Tenfold** : un prof IA (personnage : Tally) qui regarde les mains de l'enfant à la webcam, lui apprend à multiplier avec ses doigts, corrige le geste en direct, valide le résultat, et dont la perception visuelle s'améliore toute seule d'une itération à l'autre.

Phrase à retenir, celle du jury et de la vidéo : **"The child learns multiplication. The tutor learns how to see the child."**

Deux processus indépendants dans un seul repo :
- **Processus A, l'application en direct** : webcam, détection des mains, classification du geste, moteur de leçon, prof, interface. C'est le produit.
- **Processus B, le loop d'auto-amélioration** : dataset annoté, évaluation Weave, trois agents (diagnostic, patch, garde) qui réécrivent le classifieur sans humain, commit, nouvelle évaluation. C'est ce qui fait gagner.

Les deux ne partagent qu'un fichier : `classifier/rules.py`.

## 2. Ce que le hackathon juge

Critères officiels : Best Loop (l'agent se corrige et s'améliore à chaque passe), Créativité (des agents qui coopèrent), Utilité, Exécution technique, Sponsor usage réel, et Most Production-Ready deux semaines après.

Tracks visés : Best Loop Design (prix principal), Best Use of Weave, Best Use of marimo, Best Social Media demo. ARIA seulement si validé au stand W&B avant midi. TypeSafe écarté sauf surprise au kickoff.

Contraintes d'éligibilité : repo GitHub public, tout construit ce week-end (premier commit à 11h15, rien avant), W&B utilisé, présence samedi, soumission sur AGI House avant dimanche 13h, démo de 3 minutes strictes avec 1 slide maximum, vidéo de moins de 2 minutes.

Deux invariants non négociables :
1. Un score objectif par itération, visible dans Weave, calculé par des scorers en code sur un split test que personne n'optimise.
2. Un agent qui modifie quelque chose de concret entre deux itérations à partir de ce score, sans intervention humaine.

## 3. La méthode mathématique (à savoir faire avec ses mains)

**Méthode 6-10** (cœur obligatoire). Sur chaque main, pouce = 6, index = 7, majeur = 8, annulaire = 9, auriculaire = 10. Paumes face caméra, mains côte à côte. Pour A × B, on fait se toucher le doigt A d'une main et le doigt B de l'autre, bout à bout. Doigts touchés + doigts en dessous (vers le pouce) = dizaines. Doigts au-dessus (vers l'auriculaire) de chaque main multipliés entre eux = unités. Exemple 8 × 7 : doigts touchés 8 et 7, en dessous 6 et 7 sur une main (3 doigts avec le touché) et 6 sur l'autre (2 doigts avec le touché) donc 5 dizaines = 50 ; au-dessus 9 et 10 sur une main (2) et 8, 9, 10 sur l'autre (3), 2 × 3 = 6 ; total 56. Couvre 6 × 6 à 10 × 10. L'ordre des mains n'a pas d'importance mathématiquement : 7 × 8 et 8 × 7 sont acceptés tous les deux par le moteur de leçon.

**Table de 9** (deuxième priorité, seulement si le 6-10 et le loop tournent). Dix doigts numérotés 1 à 10, face caméra : main gauche 1 à 5 de l'auriculaire au pouce, main droite 6 à 10 du pouce à l'auriculaire. Pour 9 × N, on replie le doigt N. Doigts levés à gauche du replié = dizaines, à droite = unités.

**Hors périmètre ce week-end** : 11-15, 16-20, division, Chisanbop (addition et soustraction), tables 2 à 5. Ils vont dans la roadmap du README, pas dans le code.

## 4. Architecture et arborescence

```
tenfold/
  CLAUDE.md                 règles d'exécution pour Claude Code
  SPEC.md                   ce document
  README.md                 architecture, outils sponsors, liens Weave, script de démo, roadmap
  .env.example              noms des variables, sans valeurs
  .gitignore                .env, __pycache__, data/raw si jamais créé
  config.py                 WEAVE_PROJECT, WINDOW_SIZE=5, UNKNOWN_THRESHOLD, chemins
  requirements.txt

  app/                      PROCESSUS A
    main.py                 boucle principale, weave.init, orchestration
    camera.py               OpenCV, webcam, frames en mémoire uniquement
    landmarks.py            MediaPipe Hands, 21 points par main, figé après 12h
    normalize.py            recentrage poignet, échelle, fenêtre glissante, figé après 12h
    ui.py                   overlay OpenCV, panneau de raisonnement, saisie clavier, TTS optionnel

  classifier/
    rules.py                SEUL FICHIER MODIFIABLE PAR LE CRITIC
    schema.py               dataclasses d'entrée et de sortie, figé

  lesson/
    engine.py               machine à états du cours, événements, déterministe
    tutor.py                appel W&B Inference sur événement, cache
    tally.py                personnage, phrases de base, ton

  data/
    capture.py              script de constitution du dataset
    samples.jsonl           dataset, landmarks + labels, jamais d'images

  eval/
    scorers.py              4 scorers, figés après 13h
    run_eval.py             weave.Evaluation sur train et sur test, séparés

  loop/
    critic.py               orchestrateur du loop, appelle Claude Code headless
    guard.py                garde en code : fichier, import, taille du diff, pas d'accès test
    prompts/
      diagnostic.md
      patch.md
      guard.md
    nightly.sh              lance N itérations en tâche de fond

  dashboard/
    loop_dashboard.py       notebook marimo
```

### 4.1 Flux du processus A

webcam → `camera.py` (frame) → `landmarks.py` (21 points × 2 mains, gauche/droite, confiance) → `normalize.py` (points normalisés, fenêtre de 5 frames) → `classifier/rules.py` (état du geste) → `lesson/engine.py` (comparaison à l'attendu, événement) → si événement : `lesson/tutor.py` (phrase de Tally) → `ui.py`.

Latence cible : sous 100 ms hors appel du prof. Aucune image écrite sur disque ni envoyée à un modèle. Weave trace uniquement les événements (`gesture_changed`, `correct_pose`, `wrong_pose`, `answer`, `tutor_call`) plus une fenêtre sur 30 pour le debug. Jamais `@weave.op` sur chaque frame en live.

### 4.2 Flux du processus B

`data/samples.jsonl` → `eval/run_eval.py` (Evaluation Weave sur train, Evaluation Weave sur test) → Weave → W&B MCP server → agent diagnostic → agent patch → agent garde → `guard.py` → `run_eval.py --split train` → porte métrique (accepte ou annule) → `git commit` par l'orchestrateur → éval held-out en sous-processus → ...

Le critic ne lit que l'Evaluation train. Le held-out vit hors du worktree (`../tenfold-heldout/test.jsonl`, mode 700), est évalué par un sous-processus avec une seconde clé W&B (`WANDB_API_KEY_HELDOUT`) dans le projet `tenfold-heldout`, et son résultat n'est jamais transmis au critic ni utilisé pour arrêter le loop ou choisir une version. La version finale est la dernière acceptée par la porte métrique (`data/BEST_VERSION`).

## 5. Contrats de données (figés)

### 5.1 Landmarks MediaPipe

Indices fixes : poignet 0 ; bouts de doigts 4 (pouce), 8 (index), 12 (majeur), 16 (annulaire), 20 (auriculaire) ; PIP 6, 10, 14, 18 ; MCP 5, 9, 13, 17 ; pouce IP 3, MCP 2.

Numérotation 6-10 : pouce = 6, index = 7, majeur = 8, annulaire = 9, auriculaire = 10.

### 5.2 Normalisation (`normalize.py`, figé)

Par main : origine au poignet (point 0), échelle = distance poignet → MCP majeur (0 → 9). Coordonnées x, y normalisées. Le z de MediaPipe est une profondeur relative par main avec une échelle propre : il n'est jamais comparé entre deux mains. Gauche/droite décidé par la position x du poignet dans l'image miroir, pas par le label MediaPipe.

Fenêtre glissante : 5 frames (1 à 5 autorisées, le moteur de leçon n'émet rien sous 5). Si un poignet bouge de plus de 0,3 unité normalisée entre deux frames, la fenêtre est réinitialisée. Gauche/droite par position x du poignet avec hystérésis : on n'échange que si l'écart x dépasse 0,1 pendant 3 frames. `classifier/features.py` (figé) reconstruit le repère commun entre les deux mains à partir de `wrist_xy` et `scale` ; c'est la seule façon de calculer une distance entre deux bouts de doigts de mains différentes.

### 5.3 Entrée du classifieur

```python
@dataclass
class HandFrame:
    points: list[tuple[float, float, float]] | None   # 21 points normalisés (origine poignet), None si main absente
    detection_conf: float
    wrist_xy: tuple[float, float] | None              # poignet en coordonnées image miroir (0..1), pour le repère commun
    scale: float | None                               # distance poignet -> MCP majeur en coordonnées image

@dataclass
class Window:
    left: list[HandFrame]     # 5 frames, main gauche (par position x)
    right: list[HandFrame]
```

### 5.4 Sortie du classifieur

```python
@dataclass
class GestureState:
    method: str               # "6-10" | "table9" | "unknown"
    left: int | None          # 6..10 pour 6-10
    right: int | None
    contact: bool             # 6-10 uniquement
    folded: int | None        # 1..10 pour table9
    confidence: float         # 0..1
```

Si la confiance est sous `UNKNOWN_THRESHOLD`, `method = "unknown"`. Le classifieur ne connaît jamais l'exercice attendu : il dit ce qu'il voit, le moteur de leçon compare.

### 5.5 Dataset `data/samples.jsonl`

Une ligne par fenêtre :
```json
{"id": "s000123", "session": "axel_1", "angle": "front|top|side", "distance": "near|far",
 "label": {"method": "6-10", "left": 8, "right": 7, "contact": true},
 "split": "train|test", "window": [[...21 points...], ...]}
```

Classes : 25 combinaisons 6-10 (ordonnées gauche/droite), 10 états table de 9, `rest` (mains au repos), `transition`, `near_contact`, `partial_hand`. Label attendu pour `rest`, `transition`, `near_contact`, `partial_hand` : `method = "unknown"` ou `contact = false`.

Split par personne : train = Axel (tous angles) ; test = 3 à 5 personnes différentes recrutées sur place, 3 minutes chacune, tous angles. L'angle `side` est rapporté à part comme métrique de stress. Le test contient la même proportion de négatifs que le train. Fenêtres non chevauchantes (stride 5) ; l'exactitude est calculée par maintien (hold), pas par fenêtre, avec un intervalle de confiance bootstrap. Le test vit hors du repo (`../tenfold-heldout/test.jsonl`).

### 5.6 Protocole de capture (`data/capture.py`)

Enregistrement continu du début à la fin. Pour chaque classe : consigne à l'écran ("fais 8 × 7"), 3 s de préparation (frames labellisées `transition`), 1,5 s de maintien dont les 0,5 premières secondes sont jetées (frames labellisées avec la classe sur la seconde restante), classe suivante. La consigne précise la main : « MAIN GAUCHE 8, MAIN DROITE 7 » en vue miroir, pour que les labels ordonnés soient justes par construction. Trois angles × deux distances. En fin de session : 10 poses `near_contact` (doigts à 2 cm sans contact), 5 poses `partial_hand` (une seule main), 5 entrées et sorties de cadre. Tout est labellisé par construction, aucune annotation manuelle. `capture.py` importe `landmarks.py` et `normalize.py` de `app/` pour garantir que dataset et application voient exactement la même chose. Cible : 800 à 1000 fenêtres en 40 minutes.

Pas de Gemini ce week-end (coupé à la revue). Aucun label n'est créé par un modèle.

### 5.7 Scorers (`eval/scorers.py`, figés après 13h)

Six scorers, tous en code, jamais modifiables par le critic. `output=None` compte comme un échec, aucun scorer ne lève d'exception.

1. `exact_match` : sortie == label sur tous les champs. Métrique principale, et la seule que le critic doit faire monter.
2. `contact_accuracy` : champ `contact` correct (6-10 seulement).
3. `per_class_accuracy` : dictionnaire par classe (toutes les classes, même absentes), pour la matrice de confusion.
4. `false_unknown_rate` : part des positifs renvoyés `unknown`. Doit rester sous 5 %.
5. `negative_rejection_accuracy` : part des `transition`, `partial_hand`, `out_of_frame` renvoyés `unknown`.
6. `near_contact_accuracy` : part des `near_contact` avec `contact = false` et `method != unknown`.

Diagnostic (non ciblé) : `exact_match_unordered` sur {left, right}. `run_eval.py` publie `tenfold-train-vN` dans le projet `tenfold` avec le hash git en attribut, écrit `eval/last_train_report.json` (exactitude par classe, 10 pires échantillons réduits à des distances de bouts de doigts, jamais de tableaux bruts), et accepte `--local` pour tourner sans clé W&B.

## 6. Le classifieur V0

**V0** (écrit par le coéquipier, honnête, ce qu'un dev écrit en premier) : pour chaque paire (bout de doigt gauche i, bout de doigt droit j), distance 2D dans le repère commun de `features.py`, normalisée par la moyenne des tailles des deux mains. Contact si la paire de distance minimale est sous un seuil unique. Frame courante uniquement. Aucune désambiguïsation, aucune logique temporelle. Interface : fonction pure `classify(window: Window) -> GestureState`, importe uniquement math, statistics, dataclasses, typing, numpy et `classifier.features`.

V0 sera naturellement imparfaite. On ne sabote pas V0 pour fabriquer une courbe. Après V0, on sépare les erreurs « détection absente » (plafond MediaPipe, affiché comme une ligne sur le dashboard) des erreurs de règle, seules corrigeables par le loop.

La liste des leviers que le critic doit découvrir seul n'est volontairement pas dans le repo.

## 7. Le loop : trois agents, une garde, un fichier

Le critic tourne dans un worktree dépouillé `../tenfold-critic` qui contient uniquement `classifier/rules.py` (éditable), `classifier/features.py` et `classifier/schema.py` (lecture), `loop/prompts/`, `loop/smoke.py`, `loop/guard.py`, et son propre `CLAUDE.md` (`loop/CLAUDE.critic.md`). Pas de SPEC.md, pas de code d'éval hors `eval/scorers.py`, pas de `.env`, pas d'outil git, jamais le held-out. Pour tester son hypothèse, l'agent patch dispose d'une copie du train (`data/train.jsonl`) et de `python loop/train_eval.py`. Le bras aveugle n'a ni le train, ni le rapport, ni cet outil, et garde ses règles dans `loop/blind-rules.py` : il ne touche jamais `classifier/rules.py` du repo.

`loop/critic.py` (dans le repo principal) orchestre. À chaque itération :

1. Nettoie le worktree, y copie les prompts et `eval/last_train_report.json`.
2. Trois appels `claude -p` distincts, chacun tracé par un `@weave.op` (prompt + résumé de sortie, jamais l'environnement) :
   - **Diagnostic** : lit le rapport d'échecs train (le MCP W&B reste disponible en lecture pour creuser). Produit une phrase : le problème le plus fréquent et une hypothèse sur sa cause.
   - **Patch** : `--allowedTools "Read,Edit,Bash(python loop/guard.py --check),Bash(python loop/smoke.py),Bash(python loop/train_eval.py),mcp__wandb__*" --max-turns 30 --model claude-sonnet-5`, timeout 10 minutes. Une hypothèse, un patch cohérent, une justification, un effet attendu. Le prompt énonce le critère de succès (exact_match train qui monte, false_unknown_rate sous 5 %) et la checklist exacte de la garde.
   - **Garde agent** : reçoit uniquement le diagnostic et le diff, sans outil. Vérifie que le patch répond au diagnostic et ne touche à rien d'autre.
3. `loop/guard.py` (copie épinglée du repo principal) : le diff ne touche que `classifier/rules.py` (`git status --porcelain` exact) ; import OK ; diff sous 80 lignes, fichier sous 400 lignes ; liste blanche d'imports par AST (math, statistics, dataclasses, typing, numpy, classifier.features) et noms interdits (open, exec, eval, __import__, getattr, globals, sys, inspect) ; smoke sur 6 fenêtres synthétiques (deux None, une main, 3 frames, 5 valides, dégénérée, NaN) en sous-processus de 5 s avec validation des domaines ; audit des entrées d'outils et du texte de l'assistant (jamais des résultats d'outils) : aucun chemin avec `..` ou absolu hors worktree, aucune mention de `heldout` ou `test.jsonl`. Chaque échec produit une ligne `GUARD_REJECT <règle>: <ce qui s'est passé> | cause: <pourquoi> | fix: <quoi changer>`, renvoyée telle quelle avec le diff. Deux rejets consécutifs = itération échouée, on continue.
4. `run_eval.py --split train`, puis **porte métrique** : le patch est accepté seulement si `exact_match` train augmente strictement, qu'aucune classe ne perd plus de 5 points et que `false_unknown_rate` ne monte pas de plus de 2 points. Sinon le patch est jeté et le rejet est loggé comme op Weave.
5. Si accepté : copie de `rules.py` dans le repo principal, `git commit -m "critic v{n}: <diagnostic> | patch: <résumé> | expected: <effet>"` avec l'auteur `critic-agent`, mise à jour de `data/BEST_VERSION`.
6. Éval held-out en sous-processus avec `WANDB_API_KEY_HELDOUT` (projet `tenfold-heldout`), résultat jamais transmis au critic.
7. Relance l'étape 1. Pannes W&B : 3 essais avec backoff puis pause 5 minutes ; `weave.finish()` avant chaque sortie de sous-processus.

Arrêt : après N itérations. Le nightly désactive l'arrêt anticipé et garde l'annulation des régressions. Jamais sur un score test.

`loop/nightly.sh` : `caffeinate -i`, 20 itérations samedi 21h, un op Weave « heartbeat » par itération, journal dans `loop/nightly.log` (gitignoré), sans `set -e` par itération. Vérification humaine à 23h30. Répétition de 2 itérations capot fermé avant 21h. Personne ne commit sur main pendant que le loop tient le worktree.

Ablation : un second worktree `../tenfold-blind` sur la branche `ablation/blind`, même orchestrateur, agent diagnostic remplacé par « améliore rules.py » sans données d'échec, évaluations suffixées `-blind`. C'est la courbe de tête de la démo : critic informé contre critic aveugle, sur le même held-out.

`critic.py --iterations 1 --dry-run` exécute diagnostic, patch et garde, affiche le diff et ne commit pas. `critic.py --mock-claude` applique des diffs canned pour tester l'orchestration sans API.

## 8. Le moteur de leçon et le prof

`lesson/engine.py`, déterministe, tracé par Weave :
- États du cours : `intro`, `exercise_shown`, `waiting_pose`, `wrong_pose`, `correct_pose`, `waiting_answer`, `answer_correct`, `answer_wrong`, `next`.
- Exercices : liste de couples (a, b) dans 6..10. Attendu : l'ensemble {a, b}, ordre libre.
- Événements produits à partir de `GestureState` : `wrong_left_finger`, `wrong_right_finger`, `no_contact`, `hands_swapped`, `correct_pose`, plus `answer_correct` et `answer_wrong` après saisie.
- Anti-rebond : un événement n'est émis que si l'état est stable 300 ms.
- Calcul du raisonnement affiché : dizaines = (a − 5) + (b − 5), unités = (10 − a) × (10 − b), résultat.

`lesson/tutor.py` : appel W&B Inference (modèle Qwen3 235B instruct ou GPT OSS 120B, endpoint compatible OpenAI, clé W&B) uniquement sur événement, dans un thread : la phrase de secours de `tally.py` s'affiche immédiatement, la phrase du modèle la remplace par fondu de 150 ms si elle arrive sous 1,5 s. Entrée : événement, exercice, état détecté, prénom de l'enfant. Sortie : une phrase de moins de 20 mots, langage enfant, en anglais pour la démo. Cache par (méthode, exercice, événement, état détecté). Phrases de secours dans `tally.py` si l'appel échoue ou dépasse 1,5 s.

`lesson/tally.py` : le personnage. Nom Tally, ton chaleureux et court, ne dit jamais "faux", dit "presque" et indique le doigt à bouger.

## 9. L'interface (`app/ui.py`)

Canvas 1920x1080 (numpy), vidéo miroir letterboxée et assombrie par un voile noir à 35 %. Texte rendu avec PIL et une police embarquée (Nunito Bold), jamais les polices OpenCV. Palette, une couleur par état : attente `#9AA3AD`, à corriger `#F5A623`, validé `#2ECC71`, dizaines `#3B82F6`, unités `#FACC15`, texte `#FFFFFF`, bandeau sombre `rgba(15,17,21,0.75)`. Échelle : exercice 120 px, réponse 200 px, phrase de Tally 44 px, raisonnement 56 px, numéros de doigts 36 px avec contour 3 px, labels de zones 32 px. Mouvements autorisés : fondu d'état 200 ms, disparition des zones 250 ms, lignes de raisonnement à 300 ms d'écart, flash de succès 400 ms, fondu de phrase 150 ms. Rien d'autre. Pas de cartes, pas d'ombres, pas de logos.

L'UI est pilotée uniquement par l'état du moteur de leçon (le geste brut ne sert qu'à positionner les labels). Un seul élément dominant par état :

| État | Élément dominant | Ce que voit le jury à 3 m |
|---|---|---|
| intro (0:00-0:10) | numéros 6-10 sur les deux mains, sans exercice | les mains et les numéros |
| waiting_pose | deux rectangles pointillés (30 % x 55 %, centrés x = 30 %/70 %, y = 55 %, pulsation 1,2 s, labels « main gauche » / « main droite ») | bandeau gris + `8 × 7` |
| une seule main | le rectangle de la main détectée s'efface, l'autre s'éclaire ; phrase de secours « Montre-moi l'autre main » ; pas de numéros tant que les deux mains ne sont pas là | idem |
| main perdue > 500 ms | les rectangles reviennent, labels figés 300 ms puis fondu ; jamais de `wrong_pose` sur une perte | idem |
| geste inconnu | numéros en blanc, sans jugement ; après 4 s, phrase de secours « Touche les deux doigts bout à bout » | idem |
| wrong_pose | doigt fautif en orange, flèche courbe 8 px vers le bon doigt de la même main ; `hands_swapped` : pas de flèche, bandeau « Échange tes mains » | bandeau orange |
| correct_pose (verrouillé jusqu'à la réponse ou `n`) | bande de raisonnement en tiers inférieur, trois lignes à 300 ms d'écart | bandeau vert |
| answer_correct | flash vert 400 ms, `56` en 200 px (0,6 → 1,0 en 250 ms), attente de `n` | le résultat |
| answer_wrong | le bandeau reste vert, le champ tremble ±8 px 3 fois en 300 ms, « Presque, recompte les dizaines » | idem |

Bandeau haut 140 px (exercice, couleur d'état en fond à 85 %). Bandeau bas 180 px : phrase de Tally à gauche (70 %), saisie à droite (30 %), chiffres tapés à 96 px, curseur clignotant 500 ms, Entrée valide, Retour efface, 3 caractères max, Entrée sur champ vide ignorée. « Main gauche » = la gauche de l'enfant = à gauche de l'écran en vue miroir.

Touches : `n` exercice suivant, `r` répéter, `p` rejoue une fenêtre de landmarks enregistrée dans le même pipeline (filet de sécurité de la démo), `d` afficher les points bruts (jamais pendant la démo), `q` quitter. Démarrage : « Tally se réveille… » en 48 px pendant l'init, jamais visible pendant la démo. Pas de TTS sauf test concluant sur l'enceinte de la démo avant 18h.

## 10. Le dashboard (`dashboard/loop_dashboard.py`)

Notebook marimo, stocké en .py dans le repo, hébergé sur molab pour la démo, lit un instantané `data/metrics.json` commité (pas de clé W&B dans le notebook, pas de bouton « lancer une itération » sur molab). Un seul graphique au-dessus de la ligne de flottaison : `exact_match` held-out par version en vert plein 3 px, train en gris pointillé, bras aveugle (ablation) en gris plein, kNN sur landmarks en pointillé, plafond de détection en ligne horizontale, bandes d'intervalle bootstrap, axe y 0-1, axe x v0…vN, messages de commit au survol. Sous le pli : matrice de confusion de la version sélectionnée (slider), diff de `rules.py` entre deux versions avec le message du critic, panel ARIA si validé. Pré-chargé sur un second écran (ou en écran partagé 70/30) pendant la démo : on ne change jamais de fenêtre. Dernière cellule : la phrase du jury en 64 px sur fond sombre, c'est l'unique slide.

## 11. Outils, comptes, clés

| Outil | Rôle | Compte / clé | Variable |
|---|---|---|---|
| W&B Weave | traces, versions, Evaluations, comparaison | wandb.ai/authorize, un projet partagé `tenfold` | `WANDB_API_KEY`, `WANDB_ENTITY` |
| W&B MCP server | lecture optionnelle des Evaluations train par le critic | `claude mcp add -s user --transport http wandb https://mcp.withwandb.com/mcp --header "Authorization: Bearer $WANDB_API_KEY"` (scope user, jamais projet : le token ne doit pas finir dans un `.mcp.json` commité) ; en headless `--mcp-config loop/mcp.json --strict-mcp-config` | |
| W&B held-out | éval held-out en sous-processus | second compte ou clé, jamais dans l'env du critic | `WANDB_API_KEY_HELDOUT` |
| W&B Inference | prof Tally | même clé, formulaire 100 $ de crédits | `WANDB_INFERENCE_BASE_URL` |
| Claude Code headless | les trois agents du loop | clé Anthropic avec crédit | `ANTHROPIC_API_KEY` |
| marimo / molab | dashboard de démo | compte molab | |
| GitHub | repo public, historique | repo créé par Axel, coéquipier collaborateur | |
| MediaPipe, OpenCV | perception | aucun | |
| ARIA | panel dans marimo, si validé au stand | team project W&B + Smart features | |
| TypeSafe | aucun rôle identifié | | |

`.env` jamais commité. `.env.example` commité avec les noms de variables, annotés (live / loop / optionnel). `*.log`, `loop/transcripts/` et `.mcp.json` gitignorés.

## 12. Répartition

Deux propriétaires, deux Claude Code, une frontière par dossier. Chaque Claude ne modifie que les fichiers de son propriétaire ; un besoin dans le dossier de l'autre se demande dans le canal de l'équipe, jamais par un commit direct.

| Dossier / fichier | Propriétaire | Contenu |
|---|---|---|
| `classifier/schema.py`, `classifier/features.py` | **contrat commun**, figés ensemble avant 13h45 | `HandFrame` (avec `wrist_xy`, `scale`), `Window`, `GestureState`, repère commun entre les mains |
| `app/` (`camera.py`, `landmarks.py`, `normalize.py`, `capture.py`, `ui.py`, `main.py`, `replay.py`) | **Axel** | webcam, MediaPipe, normalisation, capture du dataset, interface, touche `p`, `make demo` |
| `lesson/engine.py`, `lesson/tally.py` | **Axel** | machine à états du cours, événements, phrases de secours |
| `data/samples.jsonl`, `../tenfold-heldout/test.jsonl` | **Axel** (capture), format défini par le contrat | train = Axel ; held-out = 3 à 5 inconnus, tous angles |
| `dashboard/`, `README.md`, script de démo, vidéo, soumission AGI House, stand W&B pour ARIA | **Axel** | |
| `classifier/rules.py` V0 | **Ilan** | le fichier que le loop édite ensuite |
| `eval/` (`scorers.py`, `run_eval.py`, `baseline_knn.py`, `last_train_report.json`) | **Ilan** | six scorers, éval train et held-out (sous-processus, seconde clé), `--local`, ligne kNN |
| `loop/` (`critic.py`, `guard.py`, `smoke.py`, `prompts/`, `CLAUDE.critic.md`, `mcp.json`, `nightly.sh`) | **Ilan** | les trois agents, la garde, la porte métrique, les worktrees `../tenfold-critic` et `../tenfold-blind`, le nightly |
| `lesson/tutor.py` | **Ilan** | W&B Inference dans un thread, cache, repli sur `tally.py` |
| `weave.init`, MCP W&B (scope user, `--mcp-config`), `WANDB_API_KEY_HELDOUT`, `tenfold/doctor.py`, `Makefile`, `.env.example`, stand W&B pour MCP et Sandboxes | **Ilan** | connexion aux outils sponsors |
| `SPEC.md`, `CLAUDE.md`, `TODOS.md` | **les deux** | une ligne dans le canal, puis le fichier |

Interfaces entre les deux, dans cet ordre :
1. Ilan livre `classifier/features.py` en premier : `capture.py` l'importe.
2. Axel livre `app/landmarks.py` et `app/normalize.py` figés, puis `data/samples.jsonl` ; Ilan les consomme sans les modifier.
3. Ilan livre `classifier/rules.py` V0 avec `classify(window) -> GestureState` ; Axel l'appelle depuis `app/main.py` via `data/BEST_VERSION`, jamais HEAD.
4. Axel livre `lesson/engine.py` avec ses événements ; Ilan branche `tutor.py` dessus.
5. Ilan livre `data/metrics.json` (instantané) ; Axel le lit dans le dashboard.

Ordre de construction : contrat à deux (30 min), puis deux voies parallèles sans dossier partagé. Voie Ilan : `rules.py` V0 → `eval/` → `loop/` (`--mock-claude` de bout en bout avant 16h) → `tutor.py` → `doctor`. Voie Axel : `landmarks`/`normalize` → capture → `engine` + `ui` → `replay` → dashboard → démo. Tripwire commun à 18h : aucune itération autonome commitée, les deux passent sur `loop/`.

## 13. Planning

- Avant 11h15 : comptes et clés, DM Samuel et Eros, stand W&B, aucun code métier.
- 11h15 à 11h45 : squelette, `landmarks.py`, `normalize.py`, `schema.py`, `capture.py`. Weave init. CLAUDE.md.
- 11h45 à 12h00 : go/no-go MediaPipe sur le geste réel, avec des chiffres : part des frames de contact où les deux mains sont présentes (pass si >= 90 %), taux d'inversion gauche/droite, gigue des bouts de doigts en px. Si non : table de 9 devient le cœur, aujourd'hui.
- 12h00 à 12h45 : capture du dataset complet, split par angle.
- 12h45 à 13h30 : `rules.py` V0, scorers, `run_eval.py`, première Evaluation train et test. Premier point sur la courbe.
- 13h30 à 16h00 : `critic.py`, `guard.py`, prompts, `--mock-claude` de bout en bout, première itération autonome avec porte métrique. Objectif : 3 itérations acceptées à 16h. Axel : engine et UI. Tripwire : aucune itération autonome commitée à 18h, toute l'équipe passe sur le loop.
- 16h00 à 18h30 : loop en continu. Tutor. Overlay complet, raisonnement, touche `p`, `make demo`, `make doctor`. Gel des fonctionnalités de l'app à 18h30. Si le score plafonne à 17h : simplifier la tâche, pas le loop.
- 18h30 à 21h00 : capture held-out avec 3 à 5 inconnus. marimo. Script de démo chronométré. Replay vidéo enregistré dans la salle. Répétition du nightly (2 itérations, capot fermé). Loop de nuit lancé à 21h, bras aveugle en parallèle.
- Dimanche 9h à 12h : vérifier le loop de nuit. Vidéo sous 2 min à 10h30. README complet. Trois répétitions. Soumission déposée à 12h15.
- 13h30 jugement, 15h30 finalistes, 16h30 prix. Post X et LinkedIn le soir même.

## 14. Le script de démo (3 minutes)

- 0:00 à 0:30 : "Combien font 8 × 7 ? Regardez mes mains." Geste, 56, Tally valide.
- 0:30 à 1:30 : nouvel exercice, mauvais doigt volontaire (8 et 9), orange, "Almost. Your left hand is right. Move your right finger from 9 to 7." Correction, vert, panneau de raisonnement, réponse tapée, bravo.
- 1:30 à 2:10 : "But every hand, every angle, every movement is different." Geste que V0 ratait. Bascule marimo : courbe test V0 à Vn, commits de l'agent, un diff ouvert et lu.
- 2:10 à 2:40 : retour webcam, exactement le geste raté en V0, reconnu. "No one edited this file. The tutor learned how to see its student better by itself."
- 2:40 à 3:00 : "Tonight it runs 20 more iterations while we sleep." Fin.

Réponse prête à "pourquoi pas un modèle ML ?" : "We deliberately started with an interpretable rule-based perception layer because our agent can inspect individual failures, understand why they happened, modify its own perception logic and show exactly what it changed. The loop isn't tied to rules: a future agent could decide that a learned classifier is the better representation."

## 15. Définition de "fini"

1. 8 × 7 reconnu en direct.
2. Mauvais geste volontaire correctement expliqué par Tally.
3. Bon geste passe au vert, raisonnement affiché.
4. Résultat enseigné et validé.
5. V0 réellement faible sur plusieurs classes, sans sabotage.
6. Le critic diagnostique une erreur sans humain.
7. Le critic modifie `rules.py` seul, garde en code respectée.
8. L'Evaluation se relance seule après chaque patch.
9. Plusieurs commits agent lisibles dans git.
10. Le score held-out test augmente réellement.
11. Le même geste raté par V0 réussit en version finale.
12. Démo complète sous 3 minutes, répétée trois fois.
13. Le critic aveugle fait moins bien que le critic informé sur le held-out (le graphique de tête).
14. `make doctor` puis `make demo` marchent sur une machine propre sans clé ni webcam en moins de 5 minutes.

Si ces douze points tiennent, on n'ajoute presque rien. ARIA, TypeSafe, un deuxième loop pédagogique, une interface web sont des bonus.

## 16. Ce que le README doit contenir

- Une phrase de résumé, la phrase du jury.
- Architecture : les deux schémas (processus A, processus B) en ASCII ou image.
- Section "Sponsor tools" : une ligne par outil, ce qui casse si on le retire, lien direct vers le projet Weave et vers les Evaluations.
- "Built this weekend" : premier commit, horodatages, "no code predates 11:15 Saturday". Mention explicite : rien de réutilisé d'un projet antérieur.
- "The loop" : comment lire l'historique git, comment relancer `loop/critic.py`, pourquoi le test est aveugle.
- Installation en 3 commandes.
- Script de démo.
- Roadmap : table de 9 si non faite, 11-20, division, Chisanbop, web app, micro.

## 17. Conventions de code et de commits

- Python 3.11, type hints, dataclasses, pas de framework web.
- Un module = une responsabilité, pas de logique métier dans `ui.py`.
- Commits humains : `feat:`, `fix:`, `data:`, `docs:`. Commits du critic : `critic vN: ...`, auteur `critic-agent`.
- Aucun secret dans le repo. Aucune image dans le repo.
- Pas de code mort, pas de TODO laissé : ce qui n'est pas fait est dans la roadmap du README.

## 18. CLAUDE.md (à la racine, pour les humains et leurs Claude Code)

Le contenu exact est dans `CLAUDE.md`. Le worktree du critic reçoit `loop/CLAUDE.critic.md` à la place, qui ne mentionne ni SPEC.md, ni les leviers, ni le held-out.


Toute décision non couverte ici se prend en une ligne dans le canal de l'équipe, puis se reporte dans SPEC.md. Un désaccord entre deux instances Claude se tranche par SPEC.md.

---

## 19. Amendements (revue /autoplan du 2026-09-12, intégrés ci-dessus)

F1. Test blindness is enforced, not promised. Held-out data lives in `../tenfold-heldout/test.jsonl`, outside the critic's worktree; `samples.jsonl` in the repo holds train only. Test Evaluations are published to a separate W&B project `tenfold-heldout`. `loop/guard.py` rejects any iteration whose critic transcript (`claude -p --output-format stream-json`) references `tenfold-heldout`, `test.jsonl`, or a `-test-` evaluation name.

F2. Three separate agents. Diagnostic, patch and guard are three distinct `claude -p` calls. The guard agent receives only the diagnosis text and the diff, with no tools. Each step is wrapped in a `@weave.op` in `loop/critic.py` that logs prompt, transcript summary and output.

F3. Held-out test = a different person's hands, all angles. A second person (not the dataset author) records the test session. Side angle is kept as a separate stress metric, not the headline.

F4. Capture labels drop the first 0.5 s of each 1.5 s hold (those frames are discarded, not labelled transition).

F5. Scorers are the Brief's six: exact_match, contact_accuracy, per_class_accuracy, false_unknown_rate, negative_rejection_accuracy, near_contact_accuracy.

F6. Overnight ablation: a blind critic (no failure data, diagnosis prompt gets only "improve rules.py") runs N iterations in a second git worktree on branch `ablation/blind`, evaluated on the same held-out set. Chart shows both curves.

F7. Atome's student-model loop (per-child error diagnosis and teaching-strategy memory) is the README roadmap "second loop", not built this weekend.

F7 bis. Reversed on 2026-09-13: the student model is built this weekend after all, as `lesson/scheduler.py`, and drives the course nodes of `web/course/` through `app/server.py`. Mastery per pose and per fact, spaced repetition, session shaping and the reproducible demo scenario are in scope; the second Weave loop over teaching strategy is not.

---

<!-- /autoplan PHASE 1: CEO REVIEW (SELECTIVE EXPANSION, subagent-only: Codex not installed) -->

Décisions acceptées à la revue (détail dans `docs/review-2026-09-12.md`) : porte métrique avant commit (E2/G1) ; worktree du critic dépouillé, sans git ni `python -c`, liste blanche AST (G2, G12, G13) ; seconde clé W&B pour le held-out (G4) ; `wrist_xy` + `scale` dans `HandFrame` (G3) ; 3 à 5 inconnus en held-out (E4) ; chiffres du go/no-go (E6) ; rapport d'échecs précalculé, MCP optionnel (E7) ; score par maintien + IC bootstrap (E9) ; caffeinate + heartbeat + répétition (E10) ; ligne kNN (E1) ; ablation en tête (E14) ; touche `p`, `make demo`, `make doctor`, `--local`, `--dry-run` (D8, X1-X13) ; spec UI complète (D1-D15). Coupé : table de 9 (sauf échec du go/no-go), Gemini, bouton molab ; reporté à dimanche : TTS, flèches, marque Tally. Reporté à TODOS.md : bras critic W&B Inference, app navigateur, rejeu hors ligne d'un patch.

