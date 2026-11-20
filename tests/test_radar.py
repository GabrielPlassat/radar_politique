"""Tests hors ligne : python -m pytest tests  (ou python tests/test_radar.py)"""
import json
import sys
from pathlib import Path

import feedparser
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radar import sources as S  # noqa: E402
from radar.moteur import Classifieur, charger_config, fusionner  # noqa: E402

CFG = charger_config()

DOSSIER_AN = {
    "uid": "DLR5L17N51234", "titreDossier": {"titre": "Proposition de loi visant à développer la propulsion vélique des navires",
                                               "titreChemin": "propulsion_velique"},
    "procedureParlementaire": {"libelle": "Proposition de loi ordinaire"},
    "actesLegislatifs": {"acteLegislatif": [
        {"codeActe": "AN1", "libelleActe": {"nomCanonique": "1ère lecture"}, "actesLegislatifs": {"acteLegislatif": [
            {"codeActe": "AN1-DEPOT", "libelleActe": {"nomCanonique": "Dépôt d'une proposition de loi"},
             "dateActe": "2025-06-04T00:00:00.000+02:00"},
            {"codeActe": "AN1-COM", "libelleActe": {"nomCanonique": "Travaux des commissions"},
             "actesLegislatifs": {"acteLegislatif": {"codeActe": "AN1-COM-FOND-NOMIN",
                                                     "libelleActe": {"nomCanonique": "Nomination de rapporteur"},
                                                     "dateActe": "2025-07-01T00:00:00.000+02:00",
                                                     "rapporteurs": {"rapporteur": {"acteurRef": "PA1"}}}}},
            {"codeActe": "AN1-DEBATS-DEC", "libelleActe": {"nomCanonique": "Décision"},
             "dateActe": "2026-05-13T00:00:00.000+02:00", "statutConclusion": {"libelle": "adopté"}}]}}]}}

XML_JORF = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEXTE_VERSION><META><META_COMMUN><ID>JORFTEXT000099999999</ID><NATURE>ARRETE</NATURE></META_COMMUN>
<META_SPEC><META_TEXTE_CHRONICLE><NOR>TREE2612345A</NOR><DATE_PUBLI>2026-11-19</DATE_PUBLI></META_TEXTE_CHRONICLE>
<META_TEXTE_VERSION><TITREFULL>Arr\xc3\xaat\xc3\xa9 du 17 novembre 2026 modifiant la fiche d'op\xc3\xa9ration standardis\xc3\xa9e relative aux autobus \xc3\xa9lectriques (certificats d'\xc3\xa9conomies d'\xc3\xa9nergie)</TITREFULL>
<MINISTERE>Minist\xc3\xa8re de la transition \xc3\xa9cologique</MINISTERE></META_TEXTE_VERSION></META_SPEC></META></TEXTE_VERSION>"""

XML_JORF_NOMINATION = XML_JORF.replace(b"modifiant la fiche", b"portant nomination au titre de la fiche")

RSS_SENAT = """<?xml version="1.0" encoding="ISO-8859-15"?><rss version="2.0"><channel><title>Sénat</title>
<item><title>Financement des infrastructures de transport - texte N° 155</title>
<link>https://www.senat.fr/leg/pjl26-155.html</link><description>Projet de loi-cadre</description>
<pubDate>Thu, 19 Nov 2026 08:26:34 GMT</pubDate></item>
<item><title>Améliorer les moyens d'action de l'AGRASC - texte de la commission N° 180</title>
<link>https://www.senat.fr/leg/ppl26-180.html</link><description>Saisies pénales</description>
<pubDate>Wed, 18 Nov 2026 14:35:54 GMT</pubDate></item></channel></rss>""".encode("iso-8859-15")

INITIATIVE_UE = {"id": 19220.0, "initiativeStatus": "ACTIVE", "reference": "Ares(2026)8434030",
                 "foreseenActType": "PROP_REG", "shortTitle": "Road transport smart enforcement",
                 "currentStatuses": [{"frontEndStage": "PLANNING_WORKFLOW", "receivingFeedbackStatus": "OPEN",
                                      "feedbackStartDate": "2026/10/27 11:51:00",
                                      "feedbackEndDate": "2026/11/24 23:59:59", "isCurrent": True}],
                 "initiativeTranslations": [{"language": "FR", "field": "SHORT_TITLE",
                                             "value": "Transport routier – contrôle intelligent et simplification"}]}


def test_dossier_an():
    f = S.analyser_dossier_an(DOSSIER_AN, {"PA1": "Agnès Firmin-Le Bodo"})
    assert f["id"] == "AN:DLR5L17N51234"
    assert f["date"] == "2025-06-04" and f["date_etape"] == "2026-05-13"
    assert f["statut"] == "En voie d'adoption"
    assert "Décision" in f["etape"] and "(adopté)" in f["etape"]
    assert f["rapporteurs"] == "Agnès Firmin-Le Bodo"
    assert len(json.loads(f["historique"])) == 3
    assert f["url"].endswith("/dossiers/propulsion_velique")


def test_statuts():
    assert S.statut_depuis_codes(["AN1-DEPOT"], "") == "En projet"
    assert S.statut_depuis_codes(["AN1-DEPOT", "PROM-PUB"], "") == "Publié / voté"
    assert S.statut_depuis_codes(["AN1-DEPOT", "AN1-DEBATS-DEC"], "rejeté") == "Abandonné ou rejeté"


def test_jorf_et_filtre():
    clf = Classifieur(CFG)
    f = S.analyser_xml_jorf(XML_JORF)
    assert f["type"] == "Arrêté" and f["categorie"] == "Exécutif" and f["date"] == "2026-11-19"
    g = clf.retenir(f)
    assert g and "CEE (certificats d'économies d'énergie)" in g["themes"]
    assert "Transports collectifs" in g["themes"]
    assert clf.retenir(S.analyser_xml_jorf(XML_JORF_NOMINATION)) is None  # exclusion
    assert S.analyser_xml_jorf(b"<ARTICLE><ID>x</ID></ARTICLE>") is None


def test_senat():
    flux = feedparser.parse(RSS_SENAT)
    f1, f2 = [S.analyser_entree_senat(e) for e in flux.entries]
    assert f1["id"] == "SENAT:pjl26-155" and f1["type"] == "Projet de loi" and f1["date"] == "2026-11-19"
    assert f2["statut"] == "En voie d'adoption"
    clf = Classifieur(CFG)
    assert clf.retenir(f1) is not None
    assert clf.retenir(f2) is None  # hors sujet STM


def test_ue():
    f = S.analyser_initiative_ue(INITIATIVE_UE, "Poids lourds, fret et logistique")
    assert f["id"] == "UE:19220" and f["titre"].startswith("Transport routier")
    assert "ouverte jusqu'au 2026-11-24" in f["etape"] and f["type"] == "Proposition de règlement"
    g = Classifieur(CFG).retenir(f)
    assert g["themes"].startswith("Poids lourds")


def test_mots_courts_sans_faux_positif():
    clf = Classifieur(CFG)
    th, _, _ = clf.analyser("Projet de loi relatif aux paquets de cigarettes")  # « ets » ne doit pas matcher
    assert th == []
    th, _, _ = clf.analyser("Révision de l'ETS 2 pour le bâtiment et le transport routier")
    assert "Marché carbone et fiscalité climat" in th


def test_fusion_evenements():
    clf = Classifieur(CFG)
    f = clf.retenir(S.analyser_dossier_an(DOSSIER_AN))
    df1, ev1 = fusionner(pd.DataFrame(columns=["id", "source"]), [f], "2026-11-01")
    assert ev1[0]["evenement"] == "Initialisation"
    f2 = dict(f, etape="CMP", statut="En voie d'adoption")
    df2, ev2 = fusionner(df1, [f2], "2026-11-20")
    assert ev2[0]["evenement"] == "Nouvelle étape" and ev2[0]["avant"] == f["etape"]
    assert df2.iloc[0]["premiere_detection"] == "2026-11-01" and df2.iloc[0]["dernier_changement"] == "2026-11-20"
    f3 = dict(f2, statut="Publié / voté")
    _, ev3 = fusionner(df2, [f3], "2026-11-27")
    assert ev3[0]["evenement"] == "Changement de statut"
    _, ev4 = fusionner(df2, [f2], "2026-11-27")
    assert ev4 == []  # rien n'a changé
    autre = dict(f, id="AN:AUTRE")
    _, ev5 = fusionner(df2, [autre], "2026-11-27")
    assert ev5[0]["evenement"] == "Nouveau texte"


if __name__ == "__main__":
    for nom, fn in list(globals().items()):
        if nom.startswith("test_"):
            fn()
            print("OK", nom)
