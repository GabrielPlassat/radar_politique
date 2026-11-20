"""Radar politiques publiques — Service Transport et Mobilité (ADEME)
Lancer en local / Colab : streamlit run app.py
"""
import datetime as dt
import io
import json

import requests

import pandas as pd
import streamlit as st

from radar.ademe import CARTE_AIDES_MOBILITE, chercher_aides, synthese_beneficiaires
from radar.moteur import (COLONNES_ANNOTATIONS, COLONNES_EVENEMENTS, COLONNES_TEXTES,
                          F_EVENEMENTS, F_JOURNAL, F_TEXTES, charger_config, lire_csv)
from radar.sources import STATUTS

st.set_page_config(page_title="Radar politiques publiques STM", page_icon="📡", layout="wide")
CATEGORIES = ["Exécutif", "Législatif", "Europe"]
PRIORITES = ["", "Haute", "Moyenne", "Basse"]


# ---------------------------------------------------------------- données
def version_donnees() -> str:
    """Change dès qu'un fichier de données change : le cache est alors ignoré."""
    return "|".join(str(f.stat().st_mtime) if f.exists() else "-" for f in (F_TEXTES, F_EVENEMENTS, F_JOURNAL))


@st.cache_data(ttl=600)
def charger(version: str = ""):  # noqa: ARG001 — sert de clé de cache
    textes = lire_csv(F_TEXTES, COLONNES_TEXTES)
    ev = lire_csv(F_EVENEMENTS, COLONNES_EVENEMENTS)
    jr = lire_csv(F_JOURNAL, ["date", "source", "etat", "lus", "retenus", "message"])
    return textes, ev, jr


def annotations() -> pd.DataFrame:
    """Notes de l'équipe : gardées dans la session du navigateur, JAMAIS envoyées sur GitHub."""
    if "annotations" not in st.session_state:
        st.session_state.annotations = pd.DataFrame(columns=COLONNES_ANNOTATIONS).astype({"suivi": bool})
    return st.session_state.annotations


def lire_fichier_notes(fichier) -> pd.DataFrame:
    a = pd.read_csv(fichier, dtype=str, keep_default_na=False)
    for c in COLONNES_ANNOTATIONS:
        if c not in a.columns:
            a[c] = ""
    a["suivi"] = a["suivi"].astype(str).str.lower().isin(["true", "1", "oui", "x"])
    return a[COLONNES_ANNOTATIONS]


def notes_utiles(a: pd.DataFrame) -> pd.DataFrame:
    """Ne garde que les textes cochés « suivi » ou qui portent une note."""
    a = a.copy()
    a["suivi"] = a["suivi"].fillna(False).astype(bool)
    remplis = a[["priorite", "responsable", "a_suivre"]].fillna("").astype(str).apply(lambda s: s.str.strip())
    return a[a["suivi"] | (remplis != "").any(axis=1)][COLONNES_ANNOTATIONS]


def notes_en_word(a: pd.DataFrame, textes: pd.DataFrame) -> bytes:
    """Document Word lisible : un bloc par texte annoté, classé par priorité."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.size = Pt(11)
    doc.add_heading("Radar politiques publiques STM — notes de suivi", level=1)
    doc.add_paragraph(f"Export du {dt.date.today():%d/%m/%Y}. Document interne — ne pas diffuser.")
    t = notes_utiles(a).merge(textes, on="id", how="left").fillna("")
    ordre = {"Haute": 0, "Moyenne": 1, "Basse": 2}
    t = t.assign(_o=t["priorite"].map(ordre).fillna(3)).sort_values(["_o", "categorie", "titre"])
    if t.empty:
        doc.add_paragraph("Aucun texte annoté.")
    for _, x in t.iterrows():
        doc.add_heading(x["titre"] or x["id"], level=2)
        for libelle, valeur in [("Priorité", x["priorite"]), ("Responsable", x["responsable"]),
                                ("À suivre", x["a_suivre"]), ("Statut", x["statut"]),
                                ("Étape", f"{x['etape']} ({x['date_etape']})" if x["etape"] else ""),
                                ("Catégorie / source", f"{x['categorie']} — {x['type']} ({x['source']})"),
                                ("Rapporteur(s)", x["rapporteurs"]), ("Thèmes", x["themes"]), ("Lien", x["url"])]:
            if str(valeur).strip(" ()—"):
                p = doc.add_paragraph()
                p.add_run(f"{libelle} : ").bold = True
                p.add_run(str(valeur))
    tampon = io.BytesIO()
    doc.save(tampon)
    return tampon.getvalue()


DEPOT_GITHUB = "GabrielPlassat/radar_politique"
WORKFLOW = "collecte.yml"
DELAI_MIN_MINUTES = 30


def secret(nom: str, defaut=None):
    try:
        return st.secrets[nom]
    except Exception:  # noqa: BLE001
        return defaut


def forcer_collecte() -> tuple[str, str]:
    """Demande à GitHub de lancer la collecte. Renvoie (niveau, message)."""
    jeton = secret("GITHUB_TOKEN_ACTIONS")
    if not jeton:
        return "warning", ("Bouton non configuré : ajouter `GITHUB_TOKEN_ACTIONS` dans les secrets "
                           "de l'app Streamlit (voir README).")
    h = {"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json"}
    base = f"https://api.github.com/repos/{DEPOT_GITHUB}/actions/workflows/{WORKFLOW}"
    r = requests.get(f"{base}/runs", headers=h, params={"per_page": 1}, timeout=30)
    if r.ok and r.json().get("workflow_runs"):
        run = r.json()["workflow_runs"][0]
        if run["status"] in ("queued", "in_progress", "waiting", "pending"):
            return "info", f"Une collecte est déjà en cours ([suivre]({run['html_url']}))."
        debut = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        ecart = (dt.datetime.now(dt.timezone.utc) - debut).total_seconds() / 60
        if ecart < DELAI_MIN_MINUTES:
            return "info", (f"Dernière collecte lancée il y a {ecart:.0f} min. "
                            f"Merci d'attendre {DELAI_MIN_MINUTES} min entre deux collectes.")
    p = requests.post(f"{base}/dispatches", headers=h, json={"ref": "main"}, timeout=30)
    if p.status_code == 204:
        return "success", ("Collecte lancée ✅ Elle prend 3 à 6 minutes. Rechargez ensuite la page "
                           f"([suivre sur GitHub](https://github.com/{DEPOT_GITHUB}/actions)).")
    return "error", f"GitHub a refusé ({p.status_code}) : {p.text[:200]}"


@st.cache_data(ttl=3600, show_spinner="Recherche dans l'open data ADEME…")
def aides_en_cache(expressions: tuple) -> pd.DataFrame:
    return chercher_aides(list(expressions))


def a_un_theme(colonne: pd.Series, choisis: list[str]) -> pd.Series:
    """Masque booléen : vrai si la ligne porte au moins un des thèmes choisis (sûr même si vide)."""
    return colonne.fillna("").map(lambda s: any(t in s for t in choisis)).astype(bool)


def themes_de(df: pd.DataFrame) -> list[str]:
    """Tous les thèmes de la config (même sans texte pour l'instant), puis ceux présents dans les données."""
    presents = {t.strip() for s in df["themes"].fillna("") for t in s.split(";") if t.strip()}
    config = [t["nom"] for t in charger_config()["themes"]]
    return config + sorted(presents - set(config))


def lien(url: str, texte: str = "ouvrir") -> str:
    return f"[{texte}]({url})" if url else ""


textes, evenements, journal = charger(version_donnees())
cfg = charger_config()

# ---------------------------------------------------------------- en-tête
st.title("📡 Radar politiques publiques — Transport et Mobilité")
_derniere = journal["date"].max() if not journal.empty else "—"
st.info(f"🔄 Les textes sont mis à jour **automatiquement chaque matin, du lundi au vendredi (vers 6 h)**, "
        f"par une collecte lancée depuis le [dépôt GitHub](https://github.com/GabrielPlassat/radar_politique) "
        f"(Assemblée nationale, Sénat, Journal officiel, consultations du ministère, Commission européenne). "
        f"Dernière collecte : **{_derniere}**.")
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
    with st.expander("🔄 Forcer la collecte"):
        st.caption("Relance tout de suite la collecte sur GitHub, sans attendre demain matin.")
        mdp_attendu = secret("COLLECTE_MOT_DE_PASSE")
        mdp = st.text_input("Mot de passe", type="password") if mdp_attendu else None
        if st.button("Lancer la collecte maintenant"):
            if mdp_attendu and mdp != mdp_attendu:
                st.error("Mot de passe incorrect.")
            else:
                niveau, message = forcer_collecte()
                getattr(st, niveau)(message)
        if st.button("Recharger les données affichées"):
            st.cache_data.clear()
            st.rerun()
    st.divider()
    st.header("🔒 Mes notes")
    st.caption("Confidentielles : jamais enregistrées sur GitHub ni sur le serveur. "
               "Rechargez votre fichier en début de séance, téléchargez-le en fin de séance.")
    depot = st.file_uploader("Recharger mon fichier de notes (.csv)", type="csv")
    if depot is not None and st.session_state.get("fichier_notes") != depot.file_id:
        st.session_state.annotations = lire_fichier_notes(depot)
        st.session_state.fichier_notes = depot.file_id
    nb_notes = len(notes_utiles(annotations()))
    st.write(f"{nb_notes} texte(s) annoté(s)")
    st.download_button("⬇️ Notes à recharger (.csv)", notes_utiles(annotations()).to_csv(index=False),
                       f"notes_radar_{dt.date.today():%Y-%m-%d}.csv", disabled=nb_notes == 0)
    st.download_button("⬇️ Notes lisibles (Word)", notes_en_word(annotations(), textes),
                       f"notes_radar_{dt.date.today():%Y-%m-%d}.docx", disabled=nb_notes == 0,
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    st.divider()
    st.metric("Textes dans le radar", len(textes))
    st.metric("Textes marqués « suivi »", int(annotations()["suivi"].sum()))
    st.caption("Les mots-clés se règlent dans `config/themes.yaml`.")

onglets = st.tabs(["🔔 Nouveautés", "📋 Textes", "🔎 Fiche texte", "🗓️ Synthèse du mois"])

# ---------------------------------------------------------------- 1. Nouveautés
with onglets[0]:
    c1, c2, c3 = st.columns([1, 2, 2])
    jours = c1.selectbox("Période", [7, 14, 30, 90], index=0, format_func=lambda j: f"{j} derniers jours")
    cats = c2.multiselect("Catégorie", CATEGORIES, default=CATEGORIES, key="nouv_cat")
    them = c3.multiselect("Thème", themes_de(textes), key="nouv_themes")
    seuil = (dt.date.today() - dt.timedelta(days=jours)).isoformat()
    ev = evenements[(evenements["date_detection"] >= seuil) & (evenements["evenement"] != "Initialisation")
                    & (evenements["categorie"].isin(cats))]
    if them:
        ev = ev[a_un_theme(ev["themes"], them)]
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
    f_cat = c1.multiselect("Catégorie", CATEGORIES, key="textes_cat")
    f_stat = c2.multiselect("Statut", STATUTS)
    f_them = c3.multiselect("Thème", themes_de(textes), key="textes_themes")
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
    if f_them: vue = vue[a_un_theme(vue["themes"], f_them)]
    if f_txt: vue = vue[vue["titre"].str.contains(f_txt, case=False, regex=False)]
    if f_suivi: vue = vue[vue["suivi"]]
    vue["score"] = pd.to_numeric(vue["score"], errors="coerce")
    vue = vue.sort_values(["suivi", "dernier_changement", "date_etape"], ascending=False)

    st.caption(f"{len(vue)} textes. Cochez « Suivi » et complétez les colonnes de droite.")
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
    # Les modifications du tableau sont gardées dans la session (pas d'envoi sur GitHub)
    modifs = edite[["id", "suivi", "priorite", "responsable", "a_suivre"]]
    st.session_state.annotations = pd.concat([a[~a["id"].isin(modifs["id"])], modifs], ignore_index=True)
    st.caption("🔒 Vos notes restent dans ce navigateur. Téléchargez-les avant de fermer l'onglet "
               "(menu de gauche « Mes notes »).")
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
    c1, c2, c3 = st.columns([1, 1, 1])
    fin = c2.date_input("Au", dt.date.today(), key="synth_fin")
    debut = c1.date_input("Du", fin - dt.timedelta(days=30), key="synth_debut")
    seulement_suivis = c3.toggle("Seulement les textes suivis", value=False, key="synth_suivis")
    s_them = st.multiselect("🎯 Filtrer la synthèse par thème (vide = tous les thèmes)", themes_de(textes),
                            key="synth_themes")
    a = annotations().set_index("id")
    # Un texte « a bougé » si sa dernière étape (ou sa publication) date de la période,
    # ou si le radar a détecté un changement pendant la période.
    d_deb, d_fin = debut.isoformat(), fin.isoformat()
    par_date = textes["date_etape"].between(d_deb, d_fin)
    ev = evenements[evenements["date_detection"].between(d_deb, d_fin)
                    & (evenements["evenement"] != "Initialisation")]
    sel = textes[par_date | textes["id"].isin(ev["id"])]
    if s_them:
        sel = sel[a_un_theme(sel["themes"], s_them)]
    if seulement_suivis:
        sel = sel[sel["id"].map(lambda i: i in a.index and bool(a.loc[i, "suivi"]))]
    sel = sel.sort_values("date_etape", ascending=False)

    lignes = [f"# Actu réglementaire — du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}", ""]
    if s_them:
        lignes += [f"*Thème(s) : {', '.join(s_them)}*", ""]
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
    st.caption(f"{len(sel)} textes ont bougé sur la période (étape ou publication datée de la période)"
               + (f", pour : {', '.join(s_them)}." if s_them else ", tous thèmes confondus."))
    st.download_button("⬇️ Télécharger la synthèse (Markdown)", md, f"actu_reglementaire_{fin:%Y-%m}.md")
    st.markdown(md)
