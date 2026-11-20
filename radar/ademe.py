"""Recherche des projets déjà financés par l'ADEME (open data data.ademe.fr)."""
from __future__ import annotations

import pandas as pd
import requests

API = "https://data.ademe.fr/data-fair/api/v1/datasets/{jeu}/lines"
JEU_AIDES = "les-aides-financieres-de-l'ademe"
COLONNES = ["nomBeneficiaire", "idBeneficiaire", "objet", "dispositifAide", "montant",
            "dateConvention", "_siret_infos._infos_commune.nom_departement"]
CARTE_AIDES_MOBILITE = "https://claude.ai/artifact/QKpKmJS3FkPGRoabKxiWHW"


def chercher_aides(expressions: list[str], taille: int = 100) -> pd.DataFrame:
    """Une requête par expression (recherche dans l'objet et le dispositif), résultats fusionnés."""
    lignes = {}
    for exp in [e.strip() for e in expressions if e and e.strip()]:
        q = f'"{exp}"' if " " in exp else exp
        r = requests.get(API.format(jeu=JEU_AIDES), timeout=60, params={
            "q": q, "q_fields": "objet,dispositifAide", "size": taille,
            "select": ",".join(COLONNES), "sort": "-montant"})
        r.raise_for_status()
        for l in r.json().get("results", []):
            l["recherche"] = exp
            lignes[l.get("_id") or id(l)] = l
    df = pd.DataFrame(list(lignes.values()))
    if df.empty:
        return df
    df = df.rename(columns={"_siret_infos._infos_commune.nom_departement": "departement"})
    df["montant"] = pd.to_numeric(df.get("montant"), errors="coerce").fillna(0)
    return df


def synthese_beneficiaires(df: pd.DataFrame) -> pd.DataFrame:
    """Un bénéficiaire par ligne : montant total, nombre d'aides, dispositifs, exemple d'objet."""
    if df.empty:
        return df
    g = df.sort_values("montant", ascending=False).groupby("nomBeneficiaire", as_index=False).agg(
        montant_total=("montant", "sum"),
        nb_aides=("montant", "size"),
        departement=("departement", "first") if "departement" in df else ("montant", "size"),
        dispositifs=("dispositifAide", lambda s: " ; ".join(dict.fromkeys(s.dropna().astype(str)))[:200]),
        exemple_objet=("objet", "first"),
        derniere_convention=("dateConvention", "max"),
    )
    return g.sort_values("montant_total", ascending=False).reset_index(drop=True)
