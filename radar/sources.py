"""Collecte des textes depuis les sources officielles.

Chaque fonction `collecter_xxx(cfg)` renvoie une liste de dictionnaires
au format commun (voir `fiche()`), sans filtrage thématique :
le filtrage est fait ensuite dans `radar/moteur.py`.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from html import unescape

import feedparser
import requests

ENTETES = {"User-Agent": "radar-politiques-stm/1.0 (veille ADEME Service Transport et Mobilite)"}

STATUTS = ["En projet", "En voie d'adoption", "Publié / voté", "Abandonné ou rejeté"]


# ---------------------------------------------------------------- utilitaires
def telecharger(url: str, timeout: int = 180, **kw) -> requests.Response:
    r = requests.get(url, headers=ENTETES, timeout=timeout, **kw)
    r.raise_for_status()
    return r


def nettoyer(texte: str) -> str:
    """Retire les balises HTML et les espaces superflus."""
    texte = re.sub(r"<[^>]+>", " ", unescape(texte or ""))
    return re.sub(r"\s+", " ", texte).strip()


def en_liste(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def texte_de(x) -> str:
    """Les fichiers de l'Assemblée stockent parfois les valeurs sous {'#text': ...}."""
    if isinstance(x, dict):
        return str(x.get("#text") or "")
    return "" if x is None else str(x)


def date_iso(x) -> str:
    """Ramène une date (chaîne, struct_time…) au format AAAA-MM-JJ."""
    if not x:
        return ""
    if hasattr(x, "tm_year"):
        return dt.date(x.tm_year, x.tm_mon, x.tm_mday).isoformat()
    s = str(x).strip().replace("/", "-")
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def fiche(**kw) -> dict:
    """Format commun d'un texte suivi."""
    base = dict(
        id="", source="", categorie="", type="", titre="", resume="", url="",
        date="", etape="", date_etape="", statut="En projet", rapporteurs="",
        historique="[]", themes_forces="",
    )
    base.update({k: ("" if v is None else v) for k, v in kw.items()})
    return base


# ---------------------------------------------------------------- Assemblée nationale
def _charger_deputes(url: str) -> dict:
    """Identifiant PAxxxx -> 'Prénom Nom'. Facultatif : en cas d'échec, dict vide."""
    noms = {}
    try:
        z = zipfile.ZipFile(io.BytesIO(telecharger(url).content))
        for nom in z.namelist():
            if "/acteur/" not in nom or not nom.endswith(".json"):
                continue
            a = json.loads(z.read(nom)).get("acteur", {})
            uid = texte_de(a.get("uid"))
            ident = (a.get("etatCivil") or {}).get("ident") or {}
            noms[uid] = f"{ident.get('prenom', '')} {ident.get('nom', '')}".strip()
    except Exception as e:  # noqa: BLE001
        print(f"  (noms des députés indisponibles : {e})")
    return noms


def _parcourir_actes(actes, chemin=()):
    """Parcourt l'arbre des actes législatifs et renvoie les actes 'feuilles'."""
    for a in en_liste(actes):
        if not isinstance(a, dict):
            continue
        lib = a.get("libelleActe") or {}
        libelle = lib.get("nomCanonique") or lib.get("libelleCourt") or a.get("codeActe", "")
        enfants = (a.get("actesLegislatifs") or {}).get("acteLegislatif")
        if enfants:
            yield from _parcourir_actes(enfants, chemin + (libelle,))
        else:
            yield a, chemin + (libelle,)


def statut_depuis_codes(codes: list[str], derniere_conclusion: str) -> str:
    codes_maj = [c.upper() for c in codes]
    if any(c.startswith("PROM") for c in codes_maj):
        return "Publié / voté"
    if any("RETRAIT" in c for c in codes_maj) or "rejet" in (derniere_conclusion or "").lower():
        return "Abandonné ou rejeté"
    if all(c.endswith("DEPOT") for c in codes_maj) or len(codes_maj) <= 1:
        return "En projet"
    return "En voie d'adoption"


def analyser_dossier_an(d: dict, deputes: dict | None = None) -> dict | None:
    """Transforme un dossier législatif (JSON Assemblée) en fiche."""
    deputes = deputes or {}
    uid = texte_de(d.get("uid"))
    titre_d = d.get("titreDossier") or {}
    titre = texte_de(titre_d.get("titre"))
    if not uid or not titre:
        return None
    proc = texte_de((d.get("procedureParlementaire") or {}).get("libelle"))

    historique, rapporteurs = [], []
    for acte, chemin in _parcourir_actes((d.get("actesLegislatifs") or {}).get("acteLegislatif")):
        date = date_iso(texte_de(acte.get("dateActe")))
        concl = texte_de((acte.get("statutConclusion") or {}).get("libelle"))
        historique.append({
            "date": date,
            "code": texte_de(acte.get("codeActe")),
            "etape": " › ".join(c for c in chemin if c),
            "conclusion": concl,
        })
        for r in en_liste((acte.get("rapporteurs") or {}).get("rapporteur")):
            ref = texte_de(r.get("acteurRef")) if isinstance(r, dict) else ""
            if ref:
                rapporteurs.append(deputes.get(ref, ref))

    datees = sorted([h for h in historique if h["date"]], key=lambda h: h["date"])
    if not datees:
        return None
    dernier = datees[-1]
    statut = statut_depuis_codes([h["code"] for h in datees], dernier["conclusion"])
    etape = dernier["etape"] + (f" ({dernier['conclusion']})" if dernier["conclusion"] else "")

    chemin_url = texte_de(titre_d.get("titreChemin")) or uid
    return fiche(
        id=f"AN:{uid}",
        source="AN",
        categorie="Législatif",
        type=proc or "Dossier législatif",
        titre=titre,
        url=f"https://www.assemblee-nationale.fr/dyn/17/dossiers/{chemin_url}",
        date=datees[0]["date"],
        etape=etape,
        date_etape=dernier["date"],
        statut=statut,
        rapporteurs=", ".join(dict.fromkeys(rapporteurs)),
        historique=json.dumps(datees, ensure_ascii=False),
    )


def collecter_an(cfg: dict) -> list[dict]:
    src = cfg["sources"]["an"]
    deputes = _charger_deputes(src["url_deputes"])
    z = zipfile.ZipFile(io.BytesIO(telecharger(src["url_dossiers"], timeout=600).content))
    depuis = src.get("activite_depuis", "2000-01-01")
    fiches = []
    for nom in z.namelist():
        if "dossierParlementaire" not in nom or not nom.endswith(".json"):
            continue
        try:
            d = json.loads(z.read(nom)).get("dossierParlementaire") or {}
            f = analyser_dossier_an(d, deputes)
        except Exception as e:  # noqa: BLE001
            print(f"  dossier illisible {nom} : {e}")
            continue
        if f and f["date_etape"] >= depuis:
            fiches.append(f)
    return fiches


# ---------------------------------------------------------------- Sénat (flux RSS)
TYPES_SENAT = {"pjl": "Projet de loi", "ppl": "Proposition de loi", "ppr": "Proposition de résolution",
               "r": "Rapport", "a": "Avis", "tas": "Texte adopté"}


def analyser_entree_senat(e: dict) -> dict:
    lien = e.get("link", "")
    m = re.search(r"/([a-z]+)(\d{2}-\d+[a-z0-9-]*)\.html", lien)
    cle = (m.group(1) + m.group(2)) if m else lien
    prefixe = m.group(1) if m else ""
    titre = nettoyer(e.get("title", ""))
    resume = nettoyer(e.get("summary", "") or e.get("description", ""))
    date = date_iso(e.get("published_parsed") or e.get("updated_parsed") or e.get("published"))
    t = titre.lower()
    if prefixe in ("r", "a") or "rapport" in t:
        statut, etape = "Publié / voté", "Rapport publié au Sénat"
    elif "texte de la commission" in t or "texte adopté" in t or prefixe == "tas":
        statut, etape = "En voie d'adoption", "Texte de commission / adopté au Sénat"
    else:
        statut, etape = "En projet", "Dépôt au Sénat"
    return fiche(
        id=f"SENAT:{cle}", source="Sénat", categorie="Législatif",
        type=TYPES_SENAT.get(prefixe, "Texte du Sénat"), titre=titre, resume=resume,
        url=lien, date=date, etape=etape, date_etape=date, statut=statut,
    )


def collecter_senat(cfg: dict) -> list[dict]:
    fiches = []
    for url in cfg["sources"]["senat"]["flux"]:
        flux = feedparser.parse(telecharger(url).content)
        fiches += [analyser_entree_senat(e) for e in flux.entries]
    return fiches


# ---------------------------------------------------------------- Journal officiel (DILA)
NATURES = {"DECRET": "Décret", "ARRETE": "Arrêté", "LOI": "Loi", "ORDONNANCE": "Ordonnance",
           "DECISION": "Décision", "AVIS": "Avis", "CIRCULAIRE": "Circulaire",
           "INSTRUCTION": "Instruction", "DELIBERATION": "Délibération"}


def analyser_xml_jorf(contenu: bytes) -> dict | None:
    """Lit un fichier XML 'TEXTE_VERSION' du Journal officiel."""
    try:
        racine = ET.fromstring(contenu)
    except ET.ParseError:
        return None
    if racine.tag != "TEXTE_VERSION":
        return None

    def val(balise):
        return nettoyer(racine.findtext(f".//{balise}") or "")

    ident = val("ID")
    titre = val("TITREFULL") or val("TITRE")
    if not ident or not titre:
        return None
    nature = val("NATURE").upper()
    date = date_iso(val("DATE_PUBLI") or val("DATE_TEXTE"))
    return fiche(
        id=f"JORF:{ident}", source="JORF",
        categorie="Législatif" if nature in ("LOI", "ORDONNANCE") else "Exécutif",
        type=NATURES.get(nature, nature.title() or "Texte"),
        titre=titre, resume=val("MINISTERE"),
        url=f"https://www.legifrance.gouv.fr/jorf/id/{ident}",
        date=date, etape=f"Publié au JO ({val('NOR')})" if val("NOR") else "Publié au JO",
        date_etape=date, statut="Publié / voté",
    )


def lister_archives_jorf(html: str, depuis: dt.date) -> list[str]:
    noms = sorted(set(re.findall(r"JORF_\d{8}-\d{6}\.tar\.gz", html)))
    seuil = depuis.strftime("%Y%m%d")
    return [n for n in noms if n[5:13] >= seuil]


def collecter_jorf(cfg: dict, jours: int | None = None) -> list[dict]:
    src = cfg["sources"]["jorf"]
    jours = jours or src.get("jours", 10)
    depuis = dt.date.today() - dt.timedelta(days=jours)
    fiches = []
    for nom in lister_archives_jorf(telecharger(src["url"]).text, depuis):
        archive = tarfile.open(fileobj=io.BytesIO(telecharger(src["url"] + nom).content), mode="r:gz")
        for m in archive.getmembers():
            if not m.isfile() or not m.name.endswith(".xml") or "/article/" in m.name:
                continue
            f = analyser_xml_jorf(archive.extractfile(m).read())
            if f:
                fiches.append(f)
    return fiches


# ---------------------------------------------------------------- Consultations publiques MTE
def collecter_consultations(cfg: dict) -> list[dict]:
    fiches = []
    for url in cfg["sources"]["consultations_mte"]["flux"]:
        flux = feedparser.parse(telecharger(url).content)
        for e in flux.entries:
            lien = e.get("link", "")
            date = date_iso(e.get("published_parsed") or e.get("updated_parsed"))
            fiches.append(fiche(
                id=f"CONSULT:{lien}", source="Consultations MTE", categorie="Exécutif",
                type="Consultation publique", titre=nettoyer(e.get("title", "")),
                resume=nettoyer(e.get("summary", ""))[:500], url=lien, date=date,
                etape="Consultation publique ouverte", date_etape=date, statut="En projet",
            ))
    return fiches


# ---------------------------------------------------------------- Commission européenne
TYPES_ACTES_UE = {"PROP_REG": "Proposition de règlement", "PROP_DIR": "Proposition de directive",
                  "PROP_DEC": "Proposition de décision", "DEL_REG": "Règlement délégué",
                  "IMPL_REG": "Règlement d'exécution", "COMMUNICATION": "Communication"}


def analyser_initiative_ue(it: dict, theme: str) -> dict | None:
    iid = it.get("id")
    if iid is None:
        return None
    iid = str(int(iid)) if isinstance(iid, float) else str(iid)
    titre = it.get("shortTitle") or ""
    for t in it.get("initiativeTranslations") or []:
        if t.get("language") == "FR" and t.get("field") == "SHORT_TITLE" and t.get("value"):
            titre = t["value"]
    statuts = it.get("currentStatuses") or []
    courant = next((s for s in statuts if s.get("isCurrent")), statuts[0] if statuts else {})
    stade = (courant.get("frontEndStage") or "").upper()
    ouvert = (courant.get("receivingFeedbackStatus") or "").upper() == "OPEN"
    debut, fin = date_iso(courant.get("feedbackStartDate")), date_iso(courant.get("feedbackEndDate"))

    if "ADOPTION" in stade:
        statut, etape = "En voie d'adoption", "Proposition adoptée par la Commission"
    elif "FINAL" in stade or "ADOPTED" in stade:
        statut, etape = "Publié / voté", "Acte adopté"
    else:
        statut, etape = "En projet", "Préparation par la Commission"
    if ouvert and fin:
        etape += f" — consultation ouverte jusqu'au {fin}"
    elif fin:
        etape += f" — consultation close le {fin}"

    return fiche(
        id=f"UE:{iid}", source="Commission UE", categorie="Europe",
        type=TYPES_ACTES_UE.get(it.get("foreseenActType") or "", it.get("foreseenActType") or "Initiative"),
        titre=nettoyer(titre), resume=nettoyer(it.get("snippet") or "")[:500],
        url=f"https://ec.europa.eu/info/law/better-regulation/have-your-say/initiatives/{iid}_fr",
        date=debut, etape=etape, date_etape=debut or fin, statut=statut, themes_forces=theme,
    )


def collecter_ue(cfg: dict) -> list[dict]:
    src = cfg["sources"]["commission_ue"]
    fiches = {}
    # On ignore les initiatives dont la dernière date connue a plus de 18 mois
    limite = (dt.date.today() - dt.timedelta(days=540)).isoformat()
    for recherche, theme in src["recherches"].items():
        r = telecharger(src["url"], params={"text": recherche, "size": 30, "language": "FR"}).json()
        for it in (r.get("initiativeResultDtoPage") or {}).get("content") or []:
            if (it.get("initiativeStatus") or "ACTIVE").upper() != "ACTIVE":
                continue
            f = analyser_initiative_ue(it, theme)
            if f and f["id"] not in fiches and (f["date_etape"] or "9999") >= limite:
                fiches[f["id"]] = f
    return list(fiches.values())


COLLECTEURS = {
    "an": ("AN", collecter_an),
    "senat": ("Sénat", collecter_senat),
    "jorf": ("JORF", collecter_jorf),
    "consultations_mte": ("Consultations MTE", collecter_consultations),
    "commission_ue": ("Commission UE", collecter_ue),
}
