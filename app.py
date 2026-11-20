"""Radar politiques publiques — Service Transport et Mobilité (ADEME)
Lancer en local / Colab : streamlit run app.py
"""
import base64
import datetime as dt
import json

import pandas as pd
import requests
import streamlit as st

from radar.ademe import CARTE_AIDES_MOBILITE, chercher_aides, synthese_beneficiaires
from radar.moteur import (COLONNES_ANNOTATIONS, COLONNES_EVENEMENTS, COLONNES_TEXTES, F_ANNOTATIONS,
                          F_EVENEMENTS, F_JOURNAL, F_TEXTES, charger_config, lire_csv)
from radar.sources import STATUTS

st.set_page_config(page_title="Radar politiques publiques STM", page_icon="📡", layout="wide")
CATEGORIES = ["Exécutif", "Législatif", "Europe"]
PRIORITES = ["", "Haute", "Moyenne", "Basse"]


# ---------------------------------------------------------------- données
@st.cache_data(ttl=600)
def charger():
    textes = lire_csv(F_TEXTES, COLONNES_TEXTES)
    ev = lire_csv(F_EVENEMENTS, COLONNES_EVENEMENTS)
    jr = lire_csv(F_JOURNAL, ["date", "source", "etat", "lus", "retenus", "message"])
    return textes, ev, jr


def annotations() -> pd.DataFrame:
    if "annotations" not in st.session_state:
        a = lire_csv(F_ANNOTATIONS, COLONNES_ANNOTATIONS)
        a["suivi"] = a["suivi"].astype(str).str.lower().isin(["true", "1", "oui", "x"])
        st.session_state.annotations = a
    return st.session_state.annotations


def enregistrer_annotations(a: pd.DataFrame) -> str:
    """Enregistre dans GitHub si un jeton est configuré, sinon propose un téléchargement."""
    a = a.copy()
    a["suivi"] = a["suivi"].fillna(False).astype(bool)
    notes = a[["priorite", "responsable", "a_suivre"]].fillna("").astype(str).apply(lambda s: s.str.strip())
    a = a[a["suivi"] | (notes != "").any(axis=1)]  # on ne garde que les lignes annotées
    csv = a[COLONNES_ANNOTATIONS].to_csv(index=False)
    try:
        F_ANNOTATIONS.write_text(csv, encoding="utf-8")
    except OSError:
        pass
    sec = st.secrets if hasattr(st, "secrets") else {}
    try:
        jeton, depot = sec["GITHUB_TOKEN"], sec["GITHUB_REPO"]
    except Exception:  # noqa: BLE001
        return "local"
    branche = sec.get("GITHUB_BRANCH", "main")
    url = f"https://api.github.com/repos/{depot}/contents/data/annotations.csv"
    h = {"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=h, params={"ref": branche}, timeout=30)
    corps = {"message": "Annotations mises à jour depuis l'application", "branch": branche,
             "content": base64.b64encode(csv.encode("utf-8")).decode()}
    if r.ok:
        corps["sha"] = r.json()["sha"]
    p = requests.put(url, headers=h, json=corps, timeout=30)
    return "github" if p.ok else f"erreur GitHub {p.status_code} : {p.text[:200]}"


@st.cache_data(ttl=3600, show_spinner="Recherche dans l'open data ADEME…")
def aides_en_cache(expressions: tuple) -> pd.DataFrame:
    return chercher_aides(list(expressions))


def themes_de(df: pd.DataFrame) -> list[str]:
    return sorted({t.strip() for s in df["themes"].fillna("") for t in s.split(";") if t.strip()})


def lien(url: str, texte: str = "ouvrir") -> str:
    return f"[{texte}]({url})" if url else ""


textes, evenements, journal = charger()
cfg = charger_config()

# ---------------------------------------------------------------- en-tête
st.title("📡 Radar politiques publiques — Transport et Mobilité")
if textes.empty:
    st.warning("Aucune donnée pour l'instant. Lancez une première collecte (`python collecte.py`, "
               "notebook Colab ou action GitHub « Collecte »).")
    st.stop()

with st.sidebar:
    st.header("Dernière collecte")
    if not journal.empty:
        der = journal[journal["date"] == journal["date"].max()]
        st.caption(f"le {der['date'].iloc[0]}")
        for _, j in der.iterrows():
            icone = "✅" if j["etat"] == "OK" else "⚠️"
            st.write(f"{icone} **{j['source']}** — {j['retenus']} retenus / {j['lus']} lus")
            if j["etat"] != "OK":
                st.caption(j["message"])
    st.divider()
    st.metric("Textes dans le radar", len(textes))
    st.metric("Textes marqués « suivi »", int(annotations()["suivi"].sum()))
    st.caption("Les mots-clés se règlent dans `config/themes.yaml`.")

onglets = st.tabs(["🔔 Nouveautés", "📋 Textes", "🔎 Fiche texte", "🗓️ Synthèse du mois"])

# ---------------------------------------------------------------- 1. Nouveautés
with onglets[0]:
    c1, c2, c3 = st.columns([1, 2, 2])
    jours = c1.selectbox("Période", [7, 14, 30, 90], index=0, format_func=lambda j: f"{j} derniers jours")
    cats = c2.multiselect("Catégorie", CATEGORIES, default=CATEGORIES)
    them = c3.multiselect("Thème", themes_de(textes))
    seuil = (dt.date.today() - dt.timedelta(days=jours)).isoformat()
    ev = evenements[(evenements["date_detection"] >= seuil) & (evenements["evenement"] != "Initialisation")
                    & (evenements["categorie"].isin(cats))]
    if them:
        ev = ev[ev["themes"].apply(lambda s: any(t in s for t in them))]
    suivis = set(annotations().loc[annotations()["suivi"], "id"])
    st.caption(f"{len(ev)} événements depuis le {seuil}. ⭐ = texte marqué « suivi ».")
    if ev.empty:
        st.info("Rien de nouveau sur la période. Les textes déjà présents lors de la première collecte "
                "n'apparaissent pas ici : voir l'onglet « Textes ».")
    for nature, icone in [("Changement de statut", "🔄"), ("Nouvelle étape", "➡️"), ("Nouveau texte", "🆕")]:
        bloc = ev[ev["evenement"] == nature].sort_values("date_detection", ascending=False)
        if bloc.empty:
            continue
        st.subheader(f"{icone} {nature} ({len(bloc)})")
        for _, e in bloc.iterrows():
            etoile = "⭐ " if e["id"] in suivis else ""
            detail = f"{e['avant']} → **{e['apres']}**" if e["avant"] else e["apres"]
            st.markdown(f"{etoile}**{e['titre']}**  \n"
                        f"<small>{e['source']} · {e['categorie']} · {e['themes']} · détecté le "
                        f"{e['date_detection']}</small>  \n{detail} · {lien(e['url'])}",
                        unsafe_allow_html=True)

# ---------------------------------------------------------------- 2. Textes (+ annotations)
with onglets[1]:
    c1, c2, c3, c4 = st.columns(4)
    f_cat = c1.multiselect("Catégorie ", CATEGORIES)
    f_stat = c2.multiselect("Statut", STATUTS)
    f_them = c3.multiselect("Thème ", themes_de(textes))
    f_src = c4.multiselect("Source", sorted(textes["source"].unique()))
    c5, c6 = st.columns([3, 1])
    f_txt = c5.text_input("Recherche dans le titre")
    f_suivi = c6.toggle("Seulement les suivis")

    a = annotations()
    vue = textes.merge(a, on="id", how="left")
    vue["suivi"] = vue["suivi"].fillna(False).astype(bool)
    for c in ["priorite", "responsable", "a_suivre"]:
        vue[c] = vue[c].fillna("")
    if f_cat: vue = vue[vue["categorie"].isin(f_cat)]
    if f_stat: vue = vue[vue["statut"].isin(f_stat)]
    if f_src: vue = vue[vue["source"].isin(f_src)]
    if f_them: vue = vue[vue["themes"].apply(lambda s: any(t in s for t in f_them))]
    if f_txt: vue = vue[vue["titre"].str.contains(f_txt, case=False, regex=False)]
    if f_suivi: vue = vue[vue["suivi"]]
    vue["score"] = pd.to_numeric(vue["score"], errors="coerce")
    vue = vue.sort_values(["suivi", "dernier_changement", "date_etape"], ascending=False)

    st.caption(f"{len(vue)} textes. Cochez « Suivi » et complétez les colonnes de droite, puis « Enregistrer ».")
    edite = st.data_editor(
        vue[["suivi", "priorite", "responsable", "a_suivre", "titre", "statut", "etape", "date_etape",
             "categorie", "type", "source", "themes", "rapporteurs", "dernier_changement", "url", "id"]],
        hide_index=True, width="stretch", height=520,
        disabled=["titre", "statut", "etape", "date_etape", "categorie", "type", "source", "themes",
                  "rapporteurs", "dernier_changement", "url", "id"],
        column_config={
            "suivi": st.column_config.CheckboxColumn("Suivi", width="small"),
            "priorite": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, width="small"),
            "responsable": st.column_config.TextColumn("Responsable", width="small"),
            "a_suivre": st.column_config.TextColumn("À suivre (note)", width="medium"),
            "titre": st.column_config.TextColumn("Titre", width="large"),
            "url": st.column_config.LinkColumn("Lien", display_text="ouvrir", width="small"),
            "date_etape": "Date étape", "dernier_changement": "Changé le", "id": None,
        })
    if st.button("💾 Enregistrer mes annotations", type="primary"):
        modifs = edite[["id", "suivi", "priorite", "responsable", "a_suivre"]]
        reste = a[~a["id"].isin(modifs["id"])]
        st.session_state.annotations = pd.concat([reste, modifs], ignore_index=True)
        res = enregistrer_annotations(st.session_state.annotations)
        if res == "github":
            st.success("Annotations enregistrées dans GitHub (partagées avec l'équipe).")
        elif res == "local":
            st.info("Enregistré pour cette session. Pour partager avec l'équipe, téléchargez le fichier "
                    "et déposez-le dans `data/` sur GitHub, ou configurez le jeton GitHub (voir README).")
        else:
            st.error(res)
    st.download_button("⬇️ Télécharger annotations.csv",
                       annotations()[COLONNES_ANNOTATIONS].to_csv(index=False), "annotations.csv")
    st.download_button("⬇️ Télécharger la vue filtrée (CSV)", vue.drop(columns=["historique"]).to_csv(index=False),
                       "radar_textes.csv")

# ---------------------------------------------------------------- 3. Fiche texte + acteurs financés
with onglets[2]:
    a = annotations()
    suivis = set(a.loc[a["suivi"], "id"])
    ordre = textes.assign(_s=textes["id"].isin(suivis)).sort_values(["_s", "dernier_changement"], ascending=False)
    choix = st.selectbox("Texte", ordre["id"], format_func=lambda i: ("⭐ " if i in suivis else "")
                         + ordre.set_index("id").loc[i, "titre"][:150])
    t = textes.set_index("id").loc[choix]
    st.subheader(t["titre"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Statut", t["statut"])
    c2.metric("Dernière étape", t["date_etape"] or "—")
    c3.metric("Source", t["source"])
    c4.metric("Type", (t["type"] or "—")[:30])
    st.markdown(f"**Étape :** {t['etape']}  \n**Thèmes :** {t['themes']}  \n"
                + (f"**Rapporteur(s) :** {t['rapporteurs']}  \n" if t["rapporteurs"] else "")
                + f"{lien(t['url'], 'Voir le texte sur le site officiel')}")
    if t["resume"]:
        st.caption(t["resume"])
    hist = json.loads(t["historique"] or "[]")
    if hist:
        with st.expander(f"Parcours du texte ({len(hist)} étapes)", expanded=False):
            st.dataframe(pd.DataFrame(hist)[["date", "etape", "conclusion"]], hide_index=True,
                         width="stretch")

    st.divider()
    st.subheader("Acteurs déjà financés par l'ADEME sur ce sujet")
    expr_defaut = []
    for th in [x.strip() for x in t["themes"].split(";") if x.strip()]:
        expr_defaut += next((d.get("ademe", []) for d in cfg["themes"] if d["nom"] == th), [])
    expr = st.text_input("Expressions recherchées dans l'objet des aides (séparées par des virgules)",
                         ", ".join(dict.fromkeys(expr_defaut)) or "mobilité", key=f"expr_{choix}")
    if st.button("Chercher les aides ADEME"):
        try:
            brut = aides_en_cache(tuple(e.strip() for e in expr.split(",") if e.strip()))
            syn = synthese_beneficiaires(brut)
            if syn.empty:
                st.info("Aucune aide trouvée avec ces expressions.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Bénéficiaires", len(syn))
                c2.metric("Aides", int(syn["nb_aides"].sum()))
                c3.metric("Montant total", f"{syn['montant_total'].sum() / 1e6:,.1f} M€".replace(",", " "))
                st.dataframe(syn, hide_index=True, width="stretch", column_config={
                    "montant_total": st.column_config.NumberColumn("Montant total (€)", format="%d"),
                    "exemple_objet": st.column_config.TextColumn("Exemple d'objet", width="large")})
                st.caption("Source : data.ademe.fr — « Les aides financières de l'ADEME » (depuis 2021). "
                           "Limité aux 100 plus grosses aides par expression.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Recherche impossible : {e}")
    st.markdown(f"Vue d'ensemble : [carte des aides mobilité ADEME]({CARTE_AIDES_MOBILITE})")

# ---------------------------------------------------------------- 4. Synthèse mensuelle (trame du deck)
with onglets[3]:
    st.caption("Prépare la slide « Actu réglementaire » : ce qui a bougé sur la période, "
               "classé comme dans le deck (Exécutif / Législatif / Europe × statut).")
    c1, c2, c3 = st.columns(3)
    fin = c2.date_input("Au", dt.date.today())
    debut = c1.date_input("Du", fin - dt.timedelta(days=30))
    seulement_suivis = c3.toggle("Seulement les textes suivis", value=False)
    a = annotations().set_index("id")
    ev = evenements[(evenements["date_detection"] >= debut.isoformat())
                    & (evenements["date_detection"] <= fin.isoformat())
                    & (evenements["evenement"] != "Initialisation")]
    ids = list(dict.fromkeys(ev["id"]))
    if seulement_suivis:
        ids = [i for i in ids if i in a.index and a.loc[i, "suivi"]]
    sel = textes[textes["id"].isin(ids)]

    lignes = [f"# Actu réglementaire — du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}", ""]
    for cat in CATEGORIES:
        bloc = sel[sel["categorie"] == cat]
        if bloc.empty:
            continue
        lignes += [f"## {cat}", ""]
        for statut in STATUTS:
            sous = bloc[bloc["statut"] == statut]
            if sous.empty:
                continue
            lignes += [f"### {statut}", ""]
            for _, x in sous.iterrows():
                note = a.loc[x["id"], "a_suivre"] if x["id"] in a.index else ""
                lignes.append(f"- **{x['titre']}** ({x['type']}, {x['source']})")
                lignes.append(f"  - Étape : {x['etape']} ({x['date_etape']})")
                if x["rapporteurs"]:
                    lignes.append(f"  - Rapporteur(s) : {x['rapporteurs']}")
                if note:
                    lignes.append(f"  - À suivre : {note}")
                lignes.append(f"  - Lien : {x['url']}")
            lignes.append("")
    md = "\n".join(lignes)
    st.caption(f"{len(sel)} textes ont bougé sur la période.")
    st.download_button("⬇️ Télécharger la synthèse (Markdown)", md, f"actu_reglementaire_{fin:%Y-%m}.md")
    st.markdown(md)
