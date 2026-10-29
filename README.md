# K2 Motor — version Neon + Render Free

Cette version remplace la version SQLite/Render payante. Aucun disque Render n’est nécessaire : les rendez-vous sont enregistrés dans Neon (PostgreSQL) via DATABASE_URL. SQLite reste disponible uniquement pour les tests locaux. Sur Render, l’absence de DATABASE_URL bloque l’accès à la base plutôt que de stocker silencieusement des rendez-vous sur disque temporaire.

## 1. Mettre à jour GitHub

Décompresser ce nouveau ZIP. Dans le dépôt existant, utiliser Add file → Upload files. Glisser le contenu du dossier, à la racine, puis Commit changes. Les fichiers modifiés sont app.py, requirements.txt, render.yaml, README.md, .env.example et tests/test_postgres_adapter.py. public/ reste identique. Ne pas envoyer le ZIP lui-même.

## 2. Préparer Neon

Dans votre compte Neon, créer un projet dédié k2-motor sur l’offre Free (ou utiliser un projet vide dédié). Choisir Frankfurt si proposé. Ouvrir Connect, sélectionner la base et le rôle, puis copier la chaîne de connexion PostgreSQL. Une connexion pooled est compatible. Conserver tous les paramètres SSL fournis. Copier seulement postgresql://… et ses paramètres, sans préfixe psql ni guillemets. Ne pas communiquer cette valeur : elle contient le mot de passe.

## 3. Créer un service Render gratuit

Abandonner la création de l’ancien Blueprint payant. Utiliser New → Web Service et sélectionner le dépôt GitHub mis à jour. Cela permet de vérifier visuellement le plan Free.

- Name : k2-motor
- Language : Python 3
- Branch : main
- Region : Frankfurt si disponible
- Root Directory : vide, si app.py est à la racine
- Build Command : pip install -r requirements.txt
- Start Command : gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60
- Instance Type : Free
- Aucun disque, aucune base Render à ajouter.

Ajouter les variables d’environnement avant Create Web Service :

| Nom | Valeur |
| --- | --- |
| DATABASE_URL | La chaîne PostgreSQL copiée depuis Neon |
| ADMIN_TOKEN | Un secret aléatoire généré par votre gestionnaire de mots de passe, au moins 32 caractères ASCII |
| PYTHON_VERSION | 3.12.8 |

ADMIN_TOKEN sert de clé dans l’onglet Garage. Ni elle ni DATABASE_URL ne doivent aller sur GitHub. Ne pas utiliser une ancienne variable DATABASE_PATH.

Alternative : le render.yaml mis à jour utilise plan: free et aucun disque. Avec New → Blueprint, il demande DATABASE_URL et génère ADMIN_TOKEN. Ne pas réutiliser une ancienne prévisualisation du Blueprint payant. Aucun paiement n’est effectué par le projet.

## 4. Tester

Après le statut Live, ouvrir l’URL onrender.com. Réserver, conserver le code privé, vérifier la réservation dans Garage avec ADMIN_TOKEN et tester l’annulation. Les tables sont créées automatiquement, aucun SQL à copier dans Neon. /health vérifie aussi la connexion à la base.

## Fonctionnalités et limites

Catalogue de 12 prestations, filtres, prix et durées côté serveur, créneaux lundi–samedi 9 h–18 h heure de Paris, de plus d’une heure à 90 jours à l’avance. Un véhicule à la fois, protection contre les doubles réservations par verrou de transaction PostgreSQL. Consultation et annulation par code privé, planning administrateur, statut terminé.

Pas de paiement, email/SMS automatique, compte client, congés ou gestion de plusieurs mécaniciens. Aucun CAPTCHA ni quota par IP n’est fourni. Le code privé donne accès au rendez-vous ; ne pas le partager. Les avis des captures ne sont pas repris comme avis vérifiés.

Render Free peut se mettre en veille après 15 minutes sans trafic et prendre environ une minute pour redémarrer. Les quotas Render et Neon s’appliquent ; cette configuration convient au démarrage et aux essais, sans garantie de disponibilité de production. Les rendez-vous restent dans Neon lors des redéploiements Render. Vérifier les limites et sauvegardes de votre plan Neon. Aucun transfert d’anciennes réservations SQLite n’est effectué.

## Avant ouverture publique

Compléter dans public/index.html les coordonnées, identité et mentions légales, conditions et contact pour les données personnelles. Vérifier services.json (prix indicatifs des captures, durées, pièces incluses). La vidange contient déjà le filtre à huile. Définir une politique de conservation et un processus de suppression ; aucune purge automatique n’est intégrée.

## Lancer localement

Python 3.12 :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python app.py
```

Ouvrir http://127.0.0.1:8000. DATABASE_URL peut être définie pour utiliser une base Neon de test ; sinon les tests locaux utilisent SQLite. .env.example n’est pas chargé automatiquement. Ne jamais lancer les tests contre une base contenant de vraies réservations.

```bash
python -m unittest discover -s tests -v
node --check public/app.js
```

Vérification de livraison : parcours et concurrence testés sur SQLite ; adaptation PostgreSQL et fermeture/rollback des connexions testés avec un pilote simulé. Aucun accès à votre compte Neon ni test réel sur votre base n’a eu lieu ; la réservation d’essai après déploiement reste nécessaire. Aucun contrôle visuel interactif ni publication externe effectué.

Sources : https://render.com/docs/free ; https://render.com/docs/web-services ; https://neon.com/docs/get-started/connect-neon ; https://www.psycopg.org/psycopg3/docs/basic/usage.html

Visuel public/assets/garage-hero.jpg : généré avec Imagegen intégré depuis la capture sélectionnée. Consigne : reconstruire le fond d’atelier sombre et ambré, retirer les textes/interface, voiture à droite et espace pour titre à gauche.
