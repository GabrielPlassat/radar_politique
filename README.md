# 📡 Radar politiques publiques — Service Transport et Mobilité (ADEME)

Pistes 1 et 2 de la stratégie Impact :
1. **Radar des textes** : repérer chaque jour les textes qui touchent les sujets STM.
2. **Suivi texte par texte** : détecter quand un texte change d'étape ou de statut, et préparer la slide « Actu réglementaire » à partir de là.

En plus, la fiche de chaque texte affiche **les acteurs déjà financés par l'ADEME** sur le même sujet (open data « Les aides financières de l'ADEME »).

## Ce que le radar surveille

| Source | Ce qu'on récupère | Catégorie du deck |
|---|---|---|
| Assemblée nationale (open data, dossiers législatifs, 17e législature) | Projets et propositions de loi, résolutions, missions d'information : parcours complet, dernière étape, rapporteurs | Législatif |
| Sénat (flux RSS) | Textes déposés, textes de commission, rapports | Législatif |
| Journal officiel (open data DILA) | Lois, décrets, arrêtés publiés (nominations exclues) | Exécutif / Législatif |
| Consultations publiques du ministère de la Transition écologique (flux RSS) | Projets de décrets et d'arrêtés soumis à consultation | Exécutif |
| Commission européenne (« Have your say ») | Initiatives en préparation, consultations ouvertes | Europe |

Chaque texte reçoit un **statut** identique à celui du deck (En projet / En voie d'adoption / Publié / voté / Abandonné ou rejeté) et un ou plusieurs **thèmes STM**.

## Mise en route (sans droits d'administrateur, tout dans le navigateur)

### 1. Dépôt GitHub
Le code est sur https://github.com/GabrielPlassat/radar_politique (dépôt **public** pour démarrer).

⚠️ Dépôt public = tout ce qui est enregistré dans `data/` est visible de tous, y compris `annotations.csv`.
Les textes collectés sont publics, mais **ne pas écrire de notes internes sensibles** dans les colonnes « Responsable » et « À suivre ».
Pour des notes confidentielles : passer le dépôt en privé (une seule app privée gratuite sur Streamlit), ou déplacer les annotations vers un Google Sheet.

### 2. Lancer la première collecte
1. Onglet **Actions** → activer les workflows si GitHub le demande.
2. **Collecte radar › Run workflow**. Compter 3 à 6 minutes.
3. Ensuite, la collecte tourne **seule chaque matin du lundi au vendredi** et enregistre les résultats dans `data/`.

La première collecte remplit le radar ; ses textes sont marqués « Initialisation » et n'apparaissent pas dans les nouveautés. Les vraies nouveautés commencent à la deuxième collecte.

### 3. Publier l'application (Streamlit Community Cloud)
1. share.streamlit.io → **New app** → choisir le dépôt, fichier `app.py` → **Deploy**.
2. Avec un dépôt public, l'app est accessible à toute personne qui a le lien.
3. *(Facultatif, mais recommandé)* Pour que le bouton « Enregistrer mes annotations » écrive directement dans GitHub :
   - GitHub › Settings › Developer settings › **Fine-grained tokens** › Generate : accès au seul dépôt `radar_politique`, permission **Contents : Read and write**.
   - Streamlit › app › Settings › **Secrets** :
     ```toml
     GITHUB_TOKEN = "github_pat_..."
     GITHUB_REPO = "GabrielPlassat/radar_politique"
     ```
   Sans jeton, les annotations restent dans la session ; on peut alors les télécharger et déposer `annotations.csv` dans `data/`.

### 4. Tester / régler dans Colab
Ouvrir `radar_colab.ipynb` dans Google Colab (Fichier › Ouvrir un notebook › GitHub). Il lance les tests, une collecte, et montre ce que chaque thème ramène, pour ajuster les mots-clés.

## L'application

- **🔔 Nouveautés** : nouveaux textes, nouvelles étapes et changements de statut des 7 / 14 / 30 / 90 derniers jours.
- **📋 Textes** : tous les textes du radar, filtrables. Colonnes à remplir par l'équipe : *Suivi*, *Priorité*, *Responsable*, *À suivre*.
- **🔎 Fiche texte** : parcours complet (Assemblée), rapporteurs, lien officiel, et **acteurs financés par l'ADEME** sur le sujet (bénéficiaires, montants, dispositifs). Lien vers la carte des aides mobilité.
- **🗓️ Synthèse du mois** : trame Markdown de la slide « Actu réglementaire » (Exécutif / Législatif / Europe × statut), avec les notes « À suivre ».

## Régler le radar : `config/themes.yaml`
Se modifie directement dans GitHub (crayon ✏️).
- **mots** : expressions cherchées dans les titres (singulier et pluriel).
- **poids** : 2 = thème spécifique STM, 1 = thème large.
- **exclusions** : titres ignorés (nominations, décorations…).
- **score_minimum** par source : au Journal officiel, un thème spécifique est exigé, pour limiter le bruit.
- **ademe** : expressions utilisées pour chercher les aides ADEME liées au thème.

## Fichiers
```
app.py                     application Streamlit
collecte.py                lance une collecte (python collecte.py)
config/themes.yaml         thèmes, mots-clés, sources
radar/sources.py           lecture de chaque source
radar/moteur.py            filtrage, fusion, détection des changements
radar/ademe.py             recherche dans les aides ADEME
data/textes.csv            état actuel de chaque texte (mis à jour par la collecte)
data/evenements.csv        historique des changements détectés
data/journal_collecte.csv  état de chaque source à chaque collecte
data/annotations.csv       suivi / priorité / responsable / notes de l'équipe
tests/                     tests hors ligne
radar_colab.ipynb          notebook de test et de réglage
```

## Limites connues
- **Sénat** : le flux RSS signale les dépôts et rapports, mais pas le parcours complet. Le parcours d'un texte du Sénat est suivi via l'Assemblée dès qu'il y est transmis.
- **Statuts** : déduits automatiquement des codes d'étape ; un rejet en première lecture est classé « Abandonné ou rejeté » même si la navette peut continuer (cas du PLF). Vérifier avant de reprendre dans le deck.
- **Consultations publiques MTE** : le site peut refuser les robots. Si la source reste en erreur dans le journal, la désactiver (`actif: false`).
- **Aides ADEME** : recherche par mots dans l'objet et le dispositif des aides, limitée aux 100 plus grosses aides par expression.
- **Hébergement** : GitHub et Streamlit Cloud sont des services externes. Valider avec la DSI avant d'y stocker des notes internes.
