"""Filtrage thématique, fusion avec l'historique et détection des changements."""
from __future__ import annotations

import datetime as dt
import re
import traceback
from pathlib import Path

import pandas as pd
import yaml

from .sources import COLLECTEURS

RACINE = Path(__file__).resolve().parent.parent
FICHIER_CONFIG = RACINE / "config" / "themes.yaml"
DOSSIER_DONNEES = RACINE / "data"
F_TEXTES = DOSSIER_DONNEES / "textes.csv"
F_EVENEMENTS = DOSSIER_DONNEES / "evenements.csv"
F_JOURNAL = DOSSIER_DONNEES / "journal_collecte.csv"
F_ANNOTATIONS = DOSSIER_DONNEES / "annotations.csv"

COLONNES_TEXTES = ["id", "source", "categorie", "type", "titre", "resume", "url", "date",
                   "etape", "date_etape", "statut", "rapporteurs", "themes", "score",
                   "premiere_detection", "dernier_changement", "historique"]
COLONNES_EVENEMENTS = ["date_detection", "id", "source", "categorie", "titre", "evenement",
                       "avant", "apres", "statut", "themes", "url"]
COLONNES_ANNOTATIONS = ["id", "suivi", "priorite", "responsable", "a_suivre"]


# ---------------------------------------------------------------- configuration
def charger_config(chemin: Path = FICHIER_CONFIG) -> dict:
    with open(chemin, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _motif(mots: list[str]) -> re.Pattern | None:
    mots = [str(m).strip().lower().replace("’", "'") for m in mots or [] if str(m).strip()]
    if not mots:
        return None
    mots.sort(key=len, reverse=True)
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(m) for m in mots) + r")(?!\w)")


class Classifieur:
    """Attribue des thèmes STM à un titre et calcule un score de pertinence."""

    def __init__(self, cfg: dict):
        self.themes = [(t["nom"], int(t.get("poids", 2)), _motif(t.get("mots"))) for t in cfg["themes"]]
        self.exclusion = _motif(cfg.get("exclusions"))
        self.seuils = cfg.get("score_minimum", {})
        self.renommages = {"Véhicules intermédiaires (vélis)": "Véhicules intermédiaires (vélis, catégorie L)"}

    def analyser(self, texte: str) -> tuple[list[str], int, bool]:
        t = (texte or "").lower().replace("’", "'")
        exclu = bool(self.exclusion and self.exclusion.search(t))
        trouves = [(nom, poids) for nom, poids, m in self.themes if m and m.search(t)]
        return [n for n, _ in trouves], sum(p for _, p in trouves), exclu

    def retenir(self, f: dict) -> dict | None:
        themes, score, exclu = self.analyser(f["titre"] + " " + (f.get("resume") or "")[:300])
        if f.get("themes_forces") and f["themes_forces"] not in themes:
            themes.insert(0, f["themes_forces"])
        if exclu and not f.get("themes_forces"):
            return None
        if score < int(self.seuils.get(f["source"], 1)) and not f.get("themes_forces"):
            return None
        g = {k: v for k, v in f.items() if k != "themes_forces"}
        g["themes"] = " ; ".join(themes)
        g["score"] = score
        return g


# ---------------------------------------------------------------- lecture / écriture
def lire_csv(chemin: Path, colonnes: list[str]) -> pd.DataFrame:
    if chemin.exists() and chemin.stat().st_size > 0:
        df = pd.read_csv(chemin, dtype=str, keep_default_na=False)
        for c in colonnes:
            if c not in df.columns:
                df[c] = ""
        return df[colonnes + [c for c in df.columns if c not in colonnes]]
    return pd.DataFrame(columns=colonnes)


def ecrire_csv(df: pd.DataFrame, chemin: Path) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(chemin, index=False, encoding="utf-8")


# ---------------------------------------------------------------- fusion
def fusionner(anciens: pd.DataFrame, nouveaux: list[dict],
              aujourd_hui: str) -> tuple[pd.DataFrame, list[dict]]:
    """Met à jour la table des textes et produit la liste des événements.

    La première fois qu'une source est collectée, ses textes sont marqués
    « Initialisation » (et non « Nouveau texte ») pour ne pas noyer les nouveautés.
    """
    table = {r["id"]: dict(r) for r in anciens.to_dict("records")}
    sources_connues = set(anciens["source"]) if "source" in anciens else set()
    evenements = []

    def evt(f, nature, avant="", apres=""):
        evenements.append(dict(
            date_detection=aujourd_hui, id=f["id"], source=f["source"], categorie=f["categorie"],
            titre=f["titre"], evenement=nature, avant=avant, apres=apres,
            statut=f["statut"], themes=f["themes"], url=f["url"]))

    for f in nouveaux:
        f = {k: ("" if v is None else str(v)) for k, v in f.items()}
        ancien = table.get(f["id"])
        if ancien is None:
            f["premiere_detection"] = aujourd_hui
            f["dernier_changement"] = aujourd_hui
            nature = "Nouveau texte" if f["source"] in sources_connues else "Initialisation"
            evt(f, nature, "", f["etape"])
        else:
            f["premiere_detection"] = ancien.get("premiere_detection") or aujourd_hui
            f["dernier_changement"] = ancien.get("dernier_changement") or aujourd_hui
            if (f["etape"], f["statut"]) != (ancien.get("etape", ""), ancien.get("statut", "")):
                f["dernier_changement"] = aujourd_hui
                if f["statut"] != ancien.get("statut"):
                    evt(f, "Changement de statut", ancien.get("statut", ""), f["statut"])
                else:
                    evt(f, "Nouvelle étape", ancien.get("etape", ""), f["etape"])
        table[f["id"]] = f

    df = pd.DataFrame(list(table.values()))
    for c in COLONNES_TEXTES:
        if c not in df.columns:
            df[c] = ""
    df = df[COLONNES_TEXTES].sort_values(["dernier_changement", "date_etape"], ascending=False)
    return df, evenements


def reclasser(df: pd.DataFrame, clf: Classifieur) -> pd.DataFrame:
    """Recalcule thèmes et score des textes déjà stockés avec les mots-clés actuels.
    Les textes ne sont jamais retirés ; pour la Commission UE, le thème attribué par la recherche est gardé."""
    if df.empty:
        return df
    df = df.copy()
    for i, r in df.iterrows():
        themes, score, _ = clf.analyser(f"{r['titre']} {str(r.get('resume', ''))[:300]}")
        if r["source"] == "Commission UE" and r.get("themes"):
            premier = str(r["themes"]).split(" ; ")[0]
            premier = clf.renommages.get(premier, premier)
            themes = [premier] + [t for t in themes if t != premier]
        df.at[i, "themes"] = " ; ".join(themes)
        df.at[i, "score"] = str(score)
    return df


# ---------------------------------------------------------------- collecte complète
def lancer_collecte(sources: list[str] | None = None, cfg: dict | None = None,
                    jours_jorf: int | None = None) -> dict:
    cfg = cfg or charger_config()
    clf = Classifieur(cfg)
    aujourd_hui = dt.date.today().isoformat()
    try:
        from zoneinfo import ZoneInfo
        heure = dt.datetime.now(ZoneInfo("Europe/Paris")).strftime("%H:%M")
    except Exception:  # noqa: BLE001
        heure = dt.datetime.now().strftime("%H:%M")
    anciens = reclasser(lire_csv(F_TEXTES, COLONNES_TEXTES), clf)
    sources_connues = set(anciens["source"])

    retenus, journal = [], []
    for cle, (nom, fonction) in COLLECTEURS.items():
        if sources and cle not in sources:
            continue
        if not cfg["sources"].get(cle, {}).get("actif", True):
            continue
        print(f"→ {nom} …", flush=True)
        try:
            if cle == "jorf":
                bruts = fonction(cfg, jours_jorf or (None if "JORF" in sources_connues else 30))
            else:
                bruts = fonction(cfg)
            gardes = [g for g in (clf.retenir(f) for f in bruts) if g]
            retenus += gardes
            journal.append(dict(date=aujourd_hui, heure=heure, source=nom, etat="OK", lus=len(bruts),
                                retenus=len(gardes), message=""))
            print(f"   {len(bruts)} textes lus, {len(gardes)} retenus")
        except Exception as e:  # noqa: BLE001  — une source en panne ne bloque pas les autres
            journal.append(dict(date=aujourd_hui, heure=heure, source=nom, etat="ERREUR", lus=0, retenus=0,
                                message=f"{type(e).__name__}: {e}"[:300]))
            print(f"   ERREUR : {e}")
            traceback.print_exc()

    textes, evenements = fusionner(anciens, retenus, aujourd_hui)
    ecrire_csv(textes, F_TEXTES)

    ev = lire_csv(F_EVENEMENTS, COLONNES_EVENEMENTS)
    ev = pd.concat([ev, pd.DataFrame(evenements, columns=COLONNES_EVENEMENTS)], ignore_index=True)
    ecrire_csv(ev, F_EVENEMENTS)

    jr = lire_csv(F_JOURNAL, ["date", "heure", "source", "etat", "lus", "retenus", "message"])
    jr = pd.concat([jr, pd.DataFrame(journal)], ignore_index=True).tail(500)
    ecrire_csv(jr, F_JOURNAL)

    resume = dict(textes=len(textes),
                  nouveaux=sum(e["evenement"] == "Nouveau texte" for e in evenements),
                  changements=sum(e["evenement"] in ("Nouvelle étape", "Changement de statut")
                                  for e in evenements),
                  erreurs=[j["source"] for j in journal if j["etat"] == "ERREUR"])
    print(f"\nBilan : {resume}")
    return resume
