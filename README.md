# IAQ -- Tableau de Bord de Qualite de l'Air Interieur

Guide complet pas a pas. Ce document est destine a toute personne souhaitant
deployer et faire fonctionner ce systeme de surveillance de la qualite de l'air,
meme sans experience prealable en programmation ou en electronique.

---

## 1. Presentation du projet

Ce projet permet de mesurer cinq parametres de l'air en temps reel :

| Parametre   | Capteur | Unite | Seuil Attention | Seuil Alerte |
|:------------|:--------|:------|:----------------|:-------------|
| CO2         | CCS811  | ppm   | 1000             | 2000         |
| TVOC        | CCS811  | ppb   | 300              | 600          |
| CO          | MQ-7    | ppm   | 9                | 35           |
| Temperature | DHT22   | C     | 28               | 35           |
| Humidite    | DHT22   | %     | 60               | 75           |

L'architecture est la suivante :

1. **L'ESP32-S3** (microcontroleur) lit les capteurs toutes les 10 secondes.
2. Il envoie les donnees en HTTP POST (JSON) vers le serveur.
3. **Le serveur Flask** (`app.py`) valide, stocke dans SQLite, et detecte les alertes.
4. **Le tableau de bord web** (`index.html`) affiche les graphiques en temps reel via Chart.js.
5. **La page educative** (`infos.html`) explique chaque capteur et ses seuils de sante.

---

## 2. Contenu du dossier

| Fichier / Dossier            | Lignes | Role |
|:-----------------------------|:-------|:-----|
| `app.py`                     | 530    | Serveur web Python Flask (backend). |
| `templates/index.html`       | 768    | Interface web du tableau de bord (frontend). |
| `templates/infos.html`       | 183    | Page educative des capteurs et seuils de sante. |
| `esp32_iaq/esp32_iaq_v2.ino` | 356    | Code Arduino pour l'ESP32 (firmware). |
| `requirements.txt`           | 1      | Dependance Python : Flask >= 3.0. |
| `guide-header.tex`           | 112    | En-tete LaTeX pour generer le PDF. |
| `Guide_IAQ.pdf`              | -      | Ce guide au format PDF imprimable. |
| `iaq.db`                     | -      | Base de donnees SQLite (creee automatiquement). |
| `README.md`                  | -      | Ce fichier. |

---

## 3. Installation du serveur (Windows 11)

### 3.1 Pre-requis

- **Python 3.10 ou superieur** : Telecharger sur `https://www.python.org/downloads/`.
  Lors de l'installation, cocher imperativement la case **"Add Python to PATH"**.
- **Google Chrome** ou **Microsoft Edge** pour afficher le tableau de bord.

### 3.2 Demarrage pas a pas

1. Ouvrir l'Explorateur de fichiers Windows et naviguer jusqu'au dossier `iaq_project`.
2. Cliquer dans la barre d'adresses en haut, effacer le chemin affiche, taper `cmd` et appuyer sur Entree.
   Une fenetre noire (l'invite de commandes) s'ouvre.
3. Taper la commande suivante et appuyer sur Entree (creation d'un environnement isole) :

```
python -m venv venv
```

4. Activer l'environnement :

```
venv\Scripts\activate
```

   Le texte `(venv)` doit apparaitre au debut de la ligne.

5. Installer la dependance Flask (a faire une seule fois) :

```
pip install -r requirements.txt
```

6. Lancer le serveur :

```
python app.py
```

7. Le terminal affiche `Running on http://127.0.0.1:5000`.
   Ouvrir un navigateur web et saisir cette adresse. Le tableau de bord s'affiche.

IMPORTANT : Ne pas fermer la fenetre noire du terminal. La reduire suffit.
Pour arreter le serveur, appuyer sur `Ctrl + C` dans le terminal.

### 3.3 Tester sans capteur physique

Cliquer sur le bouton **"Donnees test"** en haut du tableau de bord. Le serveur
genere automatiquement 1440 mesures simulees (24 heures de donnees realistes)
et les affiche instantanement dans les graphiques.

---

## 4. Liste complete des fonctions du serveur (`app.py`)

### 4.1 Fonctions internes

| Fonction               | Ligne | Role |
|:-----------------------|:------|:-----|
| `get_db()`             | 60    | Ouvre une connexion SQLite dans le contexte Flask. |
| `close_db()`           | 67    | Ferme la connexion en fin de requete. |
| `init_db()`            | 74    | Cree les tables `mesures` et `alertes` avec index. |
| `cleanup_old_data()`   | 104   | Supprime les donnees plus vieilles que 30 jours (au demarrage). |
| `validate_sensor_value()` | 116 | Verifie qu'une valeur est numerique et dans la plage autorisee. |
| `validate_measurement()` | 127  | Applique la validation a tous les champs capteur. |
| `verifier_alertes()`   | 141   | Compare chaque valeur aux seuils et insere les alertes dans la base. |
| `_insert_single()`     | 208   | Insere une seule mesure apres validation. |
| `_insert_batch()`      | 234   | Insere un lot de mesures (jusqu'a 100 d'un coup). |

### 4.2 Routes API (les adresses web)

| Methode | Adresse          | Description |
|:--------|:-----------------|:------------|
| GET     | `/`              | Affiche le tableau de bord HTML. |
| GET     | `/infos`         | Affiche la page educative des capteurs. |
| GET     | `/api/health`    | Retourne `{"statut":"ok"}`. Test de vie pour l'ESP32. |
| POST    | `/api/mesures`   | Recoit les donnees capteur (JSON simple ou tableau JSON). |
| GET     | `/api/data`      | Retourne les N dernieres mesures (parametre `n`, max 5000). Filtrable par date (`from`, `to`). |
| GET     | `/api/stats`     | Calcule moyenne, minimum, maximum par capteur sur une periode. |
| GET     | `/api/alertes`   | Retourne l'historique des alertes. Filtrable par capteur, niveau, et date. |
| GET     | `/api/export`    | Telecharge les mesures au format CSV (compatible Excel). Filtrable par date. |
| POST    | `/api/clear`     | Supprime toutes les mesures et alertes de la base. |
| POST    | `/api/seed`      | Genere 1440 mesures de test (mode debug uniquement). |

### 4.3 Parametres configurables (`app.py`, lignes 16 a 36)

| Variable               | Valeur par defaut | Description |
|:-----------------------|:------------------|:------------|
| `DATABASE`             | `"iaq.db"`        | Nom du fichier base de donnees. |
| `DEBUG`                | `True`            | Active le bouton "Donnees test" et les logs detailles. |
| `DATA_RETENTION_DAYS`  | `30`              | Duree de conservation des donnees en jours. |
| `SENSOR_OFFLINE_MINUTES` | `5`             | Delai avant affichage "HORS LIGNE" sur le tableau de bord. |
| `THRESHOLDS`           | Voir tableau section 1 | Seuils d'alerte par capteur. |
| `VALID_RANGES`         | ex: CO2 [0, 10000] | Plages de validation des capteurs. |

---

## 5. Liste complete des fonctions du firmware ESP32 (`esp32_iaq_v2.ino`)

### 5.1 Fonctions

| Fonction              | Ligne | Role |
|:----------------------|:------|:-----|
| `setup()`             | 71    | Initialise Watchdog, I2C (timeout 1s), broches, WiFi, capteurs. |
| `loop()`              | 107   | Boucle principale : lit, decide du ventilateur, envoie (toutes les 10s). |
| `lireCO2()`           | 168   | Lit le CO2 du CCS811 avec timeout de 500ms. |
| `lireTVOC()`          | 186   | Lit le TVOC du CCS811. |
| `lireCO()`            | 192   | Lit le MQ-7 via GPIO 34, applique la formule Rs/R0. |
| `lireTemperature()`   | 203   | Lit la temperature du DHT22. |
| `lireHumidite()`      | 210   | Lit l'humidite du DHT22. |
| `envoyerMesures()`    | 219   | Verifie le serveur, envoie le buffer si besoin, puis la mesure. |
| `verifierServeur()`   | 233   | GET `/api/health` avec timeout 3s. |
| `envoyerUneMesure()`  | 243   | Construit le JSON et POST vers `/api/mesures`. |
| `ajouterAuBuffer()`   | 269   | Stocke en RAM (max 30, ecrasement circulaire). |
| `envoyerBuffer()`     | 282   | Envoie le buffer complet en JSON Array. |
| `traiterAlertes()`    | 310   | Analyse la reponse serveur, active buzzer si alerte. |
| `connecterWiFi()`     | 339   | Connexion WiFi (40 tentatives, 20s max, Watchdog reset). |

### 5.2 Mecanismes de securite integres

- **Watchdog (15s)** : Redemarre l'ESP32 si le code se bloque.
- **Timeout I2C (1s)** : Empeche le bus I2C de bloquer indefiniment.
- **Timeout CCS811 (500ms)** : Retourne NAN au lieu de geler.
- **Buffer hors-ligne (30 mesures)** : Envoi batch a la reconnexion WiFi.
- **Calibration croisee** : `setEnvironmentalData(hum, temp)` ameliore la precision du CCS811.
- **Valeurs NAN exclues** : Les champs invalides sont omis du JSON.
- **Ventilateur automatique** : GPIO 38 active si CO2 > 2000, TVOC > 600, CO > 35, Temp > 35,
  ou Humidite > 75. Se desactive quand les valeurs redescendent.
- **Buzzer d'alerte** : 3 bips si le serveur confirme une alerte.
- **LEDs indicatrices** : Verte (GPIO 25) = tout va bien. Rouge (GPIO 26) = alerte.

### 5.3 Parametres a modifier avant de telecharger (lignes 27 a 38)

| Variable         | Valeur par defaut           | Ce qu'il faut mettre |
|:-----------------|:----------------------------|:---------------------|
| `WIFI_SSID`      | `"ton_wifi"`                | Le nom du reseau WiFi (SSID). |
| `WIFI_PASSWORD`   | `"ton_mot_de_passe"`       | Le mot de passe WiFi. |
| `SERVER_URL`     | `"http://192.168.1.100:5000/api/mesures"` | L'adresse IPv4 du PC serveur, port 5000. |
| `HEALTH_URL`     | `"http://192.168.1.100:5000/api/health"`  | Meme adresse, endpoint health. |
| `DEVICE_ID`      | `"esp32-salon"`             | Un nom pour identifier le boitier. |

Pour trouver l'adresse IPv4 du PC serveur sous Windows : ouvrir `cmd`, taper `ipconfig`,
et lire la ligne `Adresse IPv4` sous l'adaptateur WiFi ou Ethernet actif.

---

## 6. Liste complete des fonctions du frontend (`index.html`)

### 6.1 Fonctions JavaScript

| Fonction              | Ligne | Role |
|:----------------------|:------|:-----|
| `applyTheme()`        | 355   | Bascule theme sombre/clair sur tous les graphiques et l'interface. |
| `buildChartConfig()`  | 413   | Genere la configuration Chart.js avec lignes de seuil. |
| `evalState()`         | 475   | Determine le statut (OK / ATTENTION / ALERTE) d'un capteur. |
| `updateSensorStatus()`| 483   | Met a jour l'indicateur "EN LIGNE" / "HORS LIGNE". |
| `updateStatus()`      | 491   | Met a jour les cadrans de valeurs en direct et le statut global. |
| `fetchAndUpdate()`    | 521   | Telecharge les donnees et met a jour les 5 graphiques. |
| `loadStats()`         | 585   | Calcule et affiche les barres min/moy/max par capteur. |
| `loadAlertes()`       | 692   | Telecharge et affiche l'historique des alertes. |
| `updateAlertBadge()`  | 737   | Met a jour le compteur d'alertes sur l'onglet. |

### 6.2 Fonctionnalites du tableau de bord

- **3 onglets** : Tableau de bord (graphiques temps reel), Statistiques (barres comparatives),
  Historique alertes (liste filtrable).
- **5 graphiques** : CO2, TVOC, CO, Temperature, Humidite. Chacun avec lignes de seuil
  "Attention" (jaune) et "Alerte" (rouge).
- **Theme sombre / clair** : Sauvegarde dans le navigateur (localStorage).
- **Zoom interactif** : Molette de souris ou pincement tactile (plugin chartjs-plugin-zoom + Hammer.js).
- **Filtrage par date** : Champs "Du" / "Au" sur chaque onglet.
- **Export CSV** : Bouton pour telecharger les donnees au format Excel.
- **Rafraichissement automatique** : Toutes les 10 secondes sur l'onglet actif.
- **Tolerance aux pannes** : Si un capteur est deconnecte, l'interface affiche `--`.
- **Badge d'alertes** : Compteur rouge sur l'onglet "Historique alertes" (alertes des 24h).
- **Indicateur en direct** : "EN LIGNE" (vert) ou "HORS LIGNE" (rouge pulsant) en haut de page.
- **Responsive mobile** : Grille 1 colonne sous 700px.

---

## 7. Page educative (`infos.html`)

Accessible via le lien **"Comprendre les capteurs"** en haut du tableau de bord,
ou directement a l'adresse `http://127.0.0.1:5000/infos`.

Cette page explique pour chaque capteur :

- **CO2** : Gaz naturel produit par la respiration. Seuils : < 1000 ppm (OK),
  1000-2000 ppm (aerer), > 2000 ppm (air confine). Fait : une salle de classe
  avec 30 eleves depasse 2000 ppm en 30 minutes sans ventilation.
- **TVOC** : Composes organiques volatils (peintures, colles, produits menagers).
  Certains sont cancerigenes (formaldehyde, benzene). Restent eleves des semaines
  apres des travaux.
- **CO** : Gaz inodore et mortel. Produit par combustion incomplete (chaudieres,
  poeles). Seuil alerte : 35 ppm. Conseil : evacuer et appeler le 15 ou le 18.
  Environ 300 deces par an en France.
- **Temperature** : Recommandation OMS : 18-22 C. Au-dela de 35 C : risque de
  coup de chaleur.
- **Humidite** : Ideal entre 40% et 60%. Trop sec : irritation. Trop humide :
  moisissures et acariens.

Design coherent avec le tableau de bord : cartes sombres, badges de niveaux colores
(vert / jaune / rouge), encadres de conseils pratiques.

---

## 8. Cablage materiel

### 8.1 Avertissements critiques -- A lire avant tout branchement

**AVERTISSEMENT 1 -- Alimentation** :
Ne jamais utiliser une pile 9V standard. Le MQ-7 (150 mA) et le WiFi ESP32 (pics
400 mA) depassent la capacite de ces piles. Utiliser un adaptateur secteur 9V ou
12V pouvant fournir au minimum 2A.

**AVERTISSEMENT 2 -- Tension du MQ-7** :
Le MQ-7 delivre jusqu'a 5V sur sa broche A0. L'ESP32 ne tolere que 3.3V maximum.
Un diviseur de tension (resistances) est obligatoire.

**AVERTISSEMENT 3 -- Bus I2C** :
Si les cables ESP32-CCS811 depassent 15 cm, ajouter des resistances pull-up de
4.7 kOhms entre SDA et 3.3V, et entre SCL et 3.3V.

### 8.2 Schema d'alimentation

```
Adaptateur secteur (9-12V 2A)
        |
   [Diode D1] (protection inversion)
        |
   [LM2596 IN+]---[LM2596 IN-]
        |                |
   Regler la vis      GND commun
   jusqu'a 5.00V
        |
   [LM2596 OUT+]---[LM2596 OUT-]
        |                |
     +5V rail         GND rail
```

### 8.3 Branchements complets

**ESP32-S3 (alimentation)**

| Broche ESP32 | Connecter a          |
|:-------------|:---------------------|
| `5VIN`       | LM2596 OUT+ (+5V)    |
| `GND`        | LM2596 OUT- (masse)  |

**CCS811 (CO2 et TVOC) -- Alimenter en 3.3V uniquement**

| Broche CCS811 | Connecter a       |
|:--------------|:------------------|
| `VCC`         | ESP32 `3.3V`      |
| `GND`         | ESP32 `GND`       |
| `SDA`         | ESP32 `GPIO 21`   |
| `SCL`         | ESP32 `GPIO 22`   |
| `WAKE`        | ESP32 `GND` (forcer le mode actif) |

**DHT22 (Temperature et Humidite)**

| Broche DHT22 | Connecter a       |
|:-------------|:------------------|
| `VCC`        | ESP32 `3.3V`      |
| `GND`        | ESP32 `GND`       |
| `DATA`       | ESP32 `GPIO 4`    |

**MQ-7 (Monoxyde de carbone) -- DIVISEUR DE TENSION OBLIGATOIRE**

| Broche MQ-7 | Connecter a                         |
|:------------|:------------------------------------|
| `VCC`       | LM2596 OUT+ (+5V)                   |
| `GND`       | GND commun                          |
| `A0`        | Voir schema du diviseur ci-dessous  |

```
MQ-7 A0 ---[R1 = 2.2 kOhms]---+--- ESP32 GPIO 34
                               |
                        [R2 = 3.3 kOhms]
                               |
                              GND
```

Tension resultante : (3.3 / (2.2 + 3.3)) x 5V = 3.0V (securise pour l'ESP32).

**Ventilateur 5V (via MOSFET IRLZ44N)**

| Broche MOSFET | Connecter a                         |
|:--------------|:------------------------------------|
| Gate (G)      | Resistance 1 kOhm puis `GPIO 38`   |
| Drain (D)     | Fil negatif (-) du ventilateur      |
| Source (S)    | GND commun                          |

Le fil positif (+) du ventilateur va sur OUT+ (5V) du LM2596.

**Buzzer actif (via transistor 2N2222)**

| Broche 2N2222 | Connecter a                         |
|:--------------|:------------------------------------|
| Base (B)      | Resistance 1 kOhm puis `GPIO 15`   |
| Collecteur (C)| Fil negatif (-) du buzzer           |
| Emetteur (E)  | GND commun                          |

Le fil positif (+) du buzzer va sur OUT+ (5V) du LM2596.

**LEDs d'indication (optionnelles)**

| LED       | Broche ESP32 | Fonction                              |
|:----------|:-------------|:--------------------------------------|
| Verte     | `GPIO 25`    | Clignotement 3x a la connexion WiFi. |
| Rouge     | `GPIO 26`    | S'allume en cas d'alerte detectee.    |

---

## 9. Televersement du code vers l'ESP32

### 9.1 Pre-requis

- **Arduino IDE 2.x** : Telecharger sur `https://www.arduino.cc/en/software`.
- Installer le support ESP32 : Menu Fichier > Preferences > URLs de gestionnaire de cartes,
  ajouter : `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
  Puis menu Outils > Type de carte > Gestionnaire de cartes, chercher "esp32" et installer.
- Installer les bibliotheques : Menu Outils > Gerer les bibliotheques, chercher et installer :
  - `Adafruit CCS811 Library`
  - `DHT sensor library` (par Adafruit)
  - `ArduinoJson` (par Benoit Blanchon)

### 9.2 Configuration

1. Ouvrir le fichier `esp32_iaq/esp32_iaq_v2.ino` dans Arduino IDE.
2. Modifier les 3 variables obligatoires (lignes 27, 28, 32) :
   - `WIFI_SSID` : le nom du reseau WiFi.
   - `WIFI_PASSWORD` : le mot de passe du reseau WiFi.
   - `SERVER_URL` : l'adresse IP du PC ou du serveur, avec le port 5000.
3. Modifier aussi `HEALTH_URL` (ligne 33) avec la meme adresse IP.
4. Menu Outils > Type de carte : choisir `ESP32S3 Dev Module`.
5. Menu Outils > Port : choisir le port COM correspondant (ex: COM3).
6. Cliquer sur le bouton `Telecharger` (fleche droite).

### 9.3 Verification

Ouvrir le Moniteur Serie (Outils > Moniteur Serie, vitesse 115200).
L'ESP32 doit afficher :
- `[CCS811] OK` si le capteur CO2 est detecte.
- Des lignes de mesure toutes les 10 secondes (CO2, TVOC, CO, Temp, Hum).
- `[VENTILATEUR] EN MARCHE` si un seuil est depasse.
- Si le serveur est eteint : les mesures sont mises en file d'attente silencieusement.

---

## 10. Resultats des tests (sans ESP32)

### 10.1 Tests API (18 tests automatises -- tous passes)

| # | Test | Resultat |
|:--|:-----|:---------|
| 1 | `GET /api/health` | OK : `{"statut":"ok"}` |
| 2 | POST mesure valide (5 capteurs) | OK : code 201, alertes "ok" |
| 3 | POST JSON invalide (texte brut) | OK : code 400, `"Corps JSON manquant"` |
| 4 | POST hors plage (CO2=-50, Temp=999) | OK : code 400, details des 2 erreurs |
| 5 | POST batch (2 mesures valides) | OK : `lignes_inserees: 2` |
| 6 | POST sans champ capteur | OK : code 400, `"Aucun champ reconnu"` |
| 7 | GET data (n=2) | OK : 2 mesures retournees |
| 8 | GET stats | OK : moyenne, min, max pour 5 capteurs |
| 9 | GET alertes (n=2) | OK : 2 alertes avec messages |
| 10 | GET export CSV | OK : entetes + lignes |
| 11 | GET /infos | OK : page HTML "Comprendre les capteurs" |
| 12 | GET 404 sur /api/fake | OK : JSON `"Endpoint non trouve"` |
| 13 | GET 404 sur /fakepage | OK : code 404 HTML |
| 14 | POST seed | OK : `lignes_inserees: 1440` |
| 15 | GET alertes apres seed | OK : alertes generees automatiquement |
| 16 | POST clear | OK : `mesures_supprimees: 1440` |
| 17 | GET data apres clear | OK : tableaux vides |
| 18 | POST re-seed | OK : `lignes_inserees: 1440` |

### 10.2 Tests navigateur (7 verifications -- toutes passees)

| Test | Resultat |
|:-----|:---------|
| Chargement dashboard avec donnees | OK : graphiques peuples, valeurs en direct. |
| Indicateur "EN LIGNE" | OK : badge vert visible. |
| Toggle theme sombre/clair | OK : bascule effective. |
| Onglet Statistiques | OK : barres min/moy/max. |
| Onglet Historique alertes | OK : 41 alertes listees. |
| Page "Comprendre les capteurs" | OK : 5 cartes educatives. |
| Navigation retour | OK : lien retour fonctionnel. |

---

## 11. Limites connues et fonctions manquantes

| Probleme                       | Description | Impact |
|:-------------------------------|:------------|:-------|
| Pas d'authentification API     | Acces libre a `/api/clear` et `/api/seed`. | Risque de perte de donnees. |
| Nettoyage non recurrent        | `cleanup_old_data()` ne tourne qu'au demarrage. | Base grossit indefiniment. |
| Polling toutes les 10s         | `setInterval` peut accumuler des requetes. | Navigateur lent. |
| Pas d'horloge sur l'ESP32      | Mesures hors-ligne horodatees a la reception. | Horodatage decale. |
| Bruit ADC du MQ-7              | ADC interne bruite par le WiFi. | Variation 10-15% sur CO. |
| CDN externes obligatoires      | Chart.js charge depuis cdn.jsdelivr.net. | Pas de mode hors-ligne. |
| Serveur Werkzeug en debug      | Non prevu pour la production. | Securite et performance. |
| Page infos sans toggle theme   | Uniquement en mode sombre. | Incoherence avec le dashboard. |
| Seuils dupliques               | Definis dans `app.py` ET dans le `.ino`. | Maintenance a deux endroits. |

---

## 12. Ameliorations prevues (Roadmap)

Suite a l'audit final du projet (`project_audit.md`), la roadmap a ete reevaluee et
completee. Chaque probleme identifie dans l'audit a ete integre. Les phases sont
classees par ordre de priorite (securite d'abord, confort ensuite).

### Phase 1 : Corrections Critiques (Securite et Fiabilite)
1. **Authentification API (Cle API)** : Proteger les routes vulnerables (`/api/clear`,
   `/api/seed`, `/api/mesures`) par un header `X-API-KEY`.
2. **Rate limiting** : Limiter le nombre de requetes par IP pour empecher le spam
   sur `/api/mesures` (bibliotheque `flask-limiter`).
3. **CORS** : Activer `flask-cors` pour permettre l'hebergement du frontend
   separement du backend sans blocage navigateur.
4. **Correction du schema EasyEDA** : Ajouter le diviseur de tension MQ-7 et les
   resistances pull-up I2C (4.7 kOhm) au schema electronique.
5. **Nettoyage periodique BDD** : Automatiser `cleanup_old_data()` avec APScheduler
   au lieu d'une simple execution au demarrage.
6. **Fichiers de depot** : Ajouter `.gitignore` et `LICENSE` avant publication GitHub.
7. **Version pip fixe** : Figer les versions exactes dans `requirements.txt`
   (`pip freeze > requirements.txt`) pour des installations reproductibles.

### Phase 2 : Optimisation du Dashboard (UX)
8. **Theme page Infos** : Ajouter la bascule clair/sombre sur `infos.html` pour
   garantir la coherence avec le dashboard.
9. **Sources scientifiques** : Ajouter des liens vers les references (OMS, ANSES)
   sur la page `infos.html` pour justifier les seuils affiches.
10. **WebSockets (Flask-SocketIO)** : Remplacer le polling JS (`setInterval 10s`)
    par des messages pousses en temps reel.
11. **Support Hors-Ligne total** : Telecharger Chart.js, Hammer.js et les plugins
    localement pour eviter de dependre des CDN (cdn.jsdelivr.net).
12. **Decimation Chart.js** : Activer le plugin de decimation pour limiter le nombre
    de points traces (5000 points x 5 graphiques = navigateur lent).
13. **Graphiques long terme** : Ajouter des courbes de moyennes hebdomadaires et
    mensuelles pour visualiser les tendances.
14. **Notifications navigateur** : Envoyer une notification systeme push si une
    alerte critique survient pendant que l'utilisateur est sur un autre onglet.
15. **Pagination API** : Ajouter offset/page sur `/api/data` pour parcourir
    l'historique page par page au lieu de tout charger d'un coup.
16. **Compression gzip** : Activer la compression des reponses JSON pour reduire
    la bande passante (middleware Flask ou reverse proxy).

### Phase 3 : Robustesse du Firmware (ESP32)
17. **Horloge NTP** : Synchroniser l'heure via `configTime()` pour horodater les
    mesures bufferisees hors-ligne avec la bonne heure.
18. **Stockage persistant (LittleFS)** : Sauvegarder le buffer de 30 mesures dans
    la memoire flash pour ne pas les perdre en cas de reset Watchdog.
19. **Constantes nommees pour les seuils ventilateur** : Remplacer les valeurs
    brutes (ligne 140 : `2000, 600, 35, 35, 75`) par des `#define` nommes.
20. **Calibration MQ-7** : Remplacer la valeur par defaut `R0=10.0` par une
    procedure de calibration en air pur pour chaque capteur individuel.
21. **Log des erreurs HTTP** : Ajouter un `Serial.printf` quand `http.POST()`
    retourne un code different de 201 pour faciliter le debogage.
22. **Reconnexion WiFi active** : Tenter une reconnexion periodique dans `loop()`
    au lieu de verifier passivement une seule fois en debut de boucle.
23. **ADC externe ADS1115** : Convertisseur 16 bits I2C pour des lectures MQ-7
    precises, a la place de l'ADC interne bruite par le WiFi.
24. **Deep Sleep** : Mettre l'ESP32 en veille entre deux mesures pour permettre
    un fonctionnement sur batterie longue duree.
25. **Mise a jour OTA** : Permettre la mise a jour du firmware par WiFi sans
    devoir rebrancher un cable USB a chaque modification.
26. **Unification des seuils** : Definir les seuils une seule fois (cote serveur)
    et les transmettre a l'ESP32 via `/api/health` pour eviter la duplication.

### Phase 4 : Fonctionnalites Avancees
27. **Notifications Telegram** : Bot pour alerter l'utilisateur sur smartphone
    si un seuil vital (ex: CO > 35 ppm) est franchi.
28. **Hebergement Cloud** : Migrer Flask vers Render ou un VPS avec PostgreSQL
    pour liberer le PC local et recevoir les donnees depuis n'importe ou.
