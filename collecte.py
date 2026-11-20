"""Lance une collecte : python collecte.py
Options : python collecte.py --sources an senat   (seulement certaines sources)
          python collecte.py --jours-jorf 30      (relire 30 jours de Journal officiel)
"""
import argparse

from radar.moteur import lancer_collecte

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Radar politiques publiques STM")
    p.add_argument("--sources", nargs="*", help="an senat jorf consultations_mte commission_ue")
    p.add_argument("--jours-jorf", type=int, default=None)
    a = p.parse_args()
    lancer_collecte(sources=a.sources, jours_jorf=a.jours_jorf)
