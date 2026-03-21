# Audit Final du Projet IAQ

Audit realise le 21 mars 2026 sur l'ensemble des fichiers sources et valide par
18 tests API automatises et 12 verifications navigateur en direct.

---

## 1. Inventaire des fichiers

| Fichier                       | Lignes | Role |
|:------------------------------|:-------|:-----|
| [app.py](file:///home/mahdidou711/linux_data/Projets/iaq_project/app.py)                      | 530    | Serveur Flask : API REST, validation, alertes, SQLite. |
| [templates/index.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/index.html)        | 768    | Dashboard web : Chart.js, 3 onglets, theme sombre/clair. |
| [templates/infos.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/infos.html)        | 183    | Page educative : explication de chaque capteur et seuils. |
| [esp32_iaq/esp32_iaq_v2.ino](file:///home/mahdidou711/linux_data/Projets/iaq_project/esp32_iaq/esp32_iaq_v2.ino)  | 356    | Firmware ESP32 : capteurs, WiFi, buffer, watchdog. |
| [requirements.txt](file:///home/mahdidou711/linux_data/Projets/iaq_project/requirements.txt)            | 1      | Dependance unique : `flask>=3.0,<4.0`. |
| [guide-header.tex](file:///home/mahdidou711/linux_data/Projets/iaq_project/guide-header.tex)            | 112    | En-tete LaTeX pour la generation PDF. |
| [README.md](file:///home/mahdidou711/linux_data/Projets/iaq_project/README.md)                   | ~250   | Documentation complete pas a pas. |
| [Guide_IAQ.pdf](file:///home/mahdidou711/linux_data/Projets/iaq_project/Guide_IAQ.pdf)               | -      | Version PDF imprimable du README. |
| [iaq.db](file:///home/mahdidou711/linux_data/Projets/iaq_project/iaq.db)                      | -      | Base SQLite creee automatiquement. |

---

## 2. Analyse fichier par fichier

### 2.1 [app.py](file:///home/mahdidou711/linux_data/Projets/iaq_project/app.py) (Backend Flask -- 530 lignes)

**Ce qui est bien :**
- Validation stricte des entrees : type numerique, plage min/max, rejet propre avec messages.
- Requetes SQL parametrees (`?`) : aucun risque d'injection SQL.
- Ingestion batch (jusqu'a 100 mesures en un POST) : ideal pour le buffer hors-ligne.
- Export CSV natif via `/api/export` : compatible Excel sans plugin.
- 404 intelligent : JSON si URL commence par `/api/`, HTML sinon.
- Seeding intelligent : 1440 mesures gaussiennes avec pics de CO2 et temperature.
- Seuils configurables en haut du fichier (lignes 22-36).

**Ce qui n'est pas bien :**
- [cleanup_old_data()](file:///home/mahdidou711/linux_data/Projets/iaq_project/app.py#104-112) (ligne 104) : ne s'execute qu'une fois au demarrage. Pas de tache planifiee.
- Serveur Werkzeug en debug : `app.run(debug=True)` n'est pas fait pour la production.
- Pas de CORS : si le frontend est heberge separement, les requetes seront bloquees.
- Pas de pagination/offset sur `/api/data` : impossible de parcourir l'historique par pages.

**Ce qui manque :**
- Aucune authentification : `/api/clear` et `/api/seed` sont accessibles par tout le reseau.
- Pas de rate limiting : un script malveillant peut spammer `/api/mesures`.
- Pas de compression gzip sur les reponses JSON.

**Resultats des tests live (18 tests) :**

| # | Test | Attendu | Resultat |
|:--|:-----|:--------|:---------|
| 1 | `GET /api/health` | `{"statut":"ok"}` | PASSE |
| 2 | POST mesure valide | Code 201, alertes "ok" | PASSE |
| 3 | POST JSON invalide | Code 400, `"Corps JSON manquant"` | PASSE |
| 4 | POST hors plage (CO2=-50, Temp=999) | Code 400, details des erreurs | PASSE |
| 5 | POST batch (2 mesures) | `lignes_inserees: 2` | PASSE |
| 6 | POST aucun champ capteur | Code 400, `"Aucun champ reconnu"` | PASSE |
| 7 | `GET /api/data?n=2` | 2 mesures avec labels | PASSE |
| 8 | `GET /api/stats` | Moyenne, min, max par capteur | PASSE |
| 9 | `GET /api/alertes?n=2` | 2 alertes avec message | PASSE |
| 10 | `GET /api/export` | Entetes CSV + lignes | PASSE |
| 11 | `GET /infos` | Page HTML `<title>IAQ — Comprendre les capteurs</title>` | PASSE |
| 12 | `GET /api/nonexistent` | Code 404, `"Endpoint non trouve"` | PASSE |
| 13 | `GET /fakepage` | Code 404 HTML | PASSE |
| 14 | `POST /api/seed` | `lignes_inserees: 1440` | PASSE |
| 15 | Alertes apres seed | Alertes generees automatiquement | PASSE |
| 16 | `POST /api/clear` | `mesures_supprimees: 1440` | PASSE |
| 17 | Data apres clear | Tableaux vides `[]` | PASSE |
| 18 | Re-seed | `lignes_inserees: 1440` | PASSE |

**Bilan : 18/18 tests passes. Zero erreur.**

---

### 2.2 [templates/index.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/index.html) (Dashboard -- 768 lignes)

**Ce qui est bien :**
- Design pro avec variables CSS : theme sombre et clair harmonieux.
- 5 graphiques Chart.js avec lignes de seuil "Attention" (jaune) et "Alerte" (rouge).
- Zoom molette + pinch tactile (chartjs-plugin-zoom + Hammer.js).
- 3 onglets : Tableau de bord, Statistiques (barres min/moy/max), Historique alertes.
- Badge compteur d'alertes sur l'onglet "Historique alertes".
- Indicateur "EN LIGNE" / "HORS LIGNE" avec animation pulse.
- Export CSV en un clic.
- Responsive mobile (grille 1 colonne sous 700px).
- Tolerance aux pannes : `null` affiche `--` au lieu de crash.
- localStorage pour sauvegarder le theme.

**Ce qui n'est pas bien :**
- `setInterval(10000)` sans protection (ligne 756) : requetes cumulables si serveur lent.
- CDN externes obligatoires (chart.js, hammer.js, etc.) : pas de fallback offline.
- Pas de decimation sur gros volumes : 5000 points x 5 graphiques = 25000 elements DOM.

**Ce qui manque :**
- Pas de graphique long terme (moyennes hebdomadaires/mensuelles).
- Pas de notifications navigateur pour les alertes critiques.

**Resultats des tests navigateur :**

| Test | Resultat |
|:-----|:---------|
| Chargement dashboard | PASSE -- Graphiques peuples, valeurs en direct affichees. |
| Indicateur "EN LIGNE" | PASSE -- Badge vert visible avec horodatage MAJ. |
| Bouton "Donnees test" | PASSE -- Present et fonctionnel. |
| Toggle theme sombre/clair | PASSE -- Bascule effective, icone change. |
| Onglet Statistiques | PASSE -- Barres min/moy/max pour 5 capteurs. |
| Onglet Historique alertes | PASSE -- 41 alertes listees avec filtrage. |
| Lien "Comprendre les capteurs" | PASSE -- Navigue vers /infos. |

---

### 2.3 [templates/infos.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/infos.html) (Page educative -- 183 lignes)

**Ce qui est bien :**
- 5 cartes de capteurs avec explications claires : CO2, TVOC, CO, Temperature, Humidite.
- Badges de niveaux colores (Vert OK, Jaune Attention, Rouge Alerte).
- Encadres conseils bleus avec donnees factuelles (ex: "420 ppm en exterieur").
- Avertissement CO : "inodore et mortel", conseil d'evacuation et numero d'urgence.
- Design coherent avec le dashboard (memes CSS variables).
- Lien retour vers le dashboard.
- Responsive (max-width adaptatif).

**Ce qui manque :**
- Pas de toggle theme clair/sombre : uniquement mode sombre.
- Pas de lien vers des sources scientifiques.

**Resultat du test navigateur : PASSE -- Page rendue correctement, 5 cartes visibles.**

---

### 2.4 [esp32_iaq/esp32_iaq_v2.ino](file:///home/mahdidou711/linux_data/Projets/iaq_project/esp32_iaq/esp32_iaq_v2.ino) (Firmware -- 356 lignes)

**Ce qui est bien :**
- Watchdog Timer 15s : `esp_task_wdt_init(15, true)` avec reset dans 5 fonctions.
- Timeout I2C : `Wire.setTimeOut(1000)` empeche le blocage du bus.
- Lecture CCS811 non-bloquante : timeout 500ms dans [lireCO2()](file:///home/mahdidou711/linux_data/Projets/iaq_project/esp32_iaq/esp32_iaq_v2.ino#167-184).
- Buffer circulaire de 30 mesures en RAM : envoi batch a la reconnexion.
- Valeurs NAN exclues du JSON (pas de donnees invalides envoyees).
- Ventilateur automatique autonome (ne depend pas du serveur).
- Calibration croisee CCS811 : `setEnvironmentalData(hum, temp)` ameliore la precision.
- Buzzer d'alerte : 3 bips rapides si le serveur confirme une alerte "alert".
- LEDs indicatrices (GPIO 25/26) pour retour visuel sans moniteur serie.
- Commentaires abondants (chaque variable et fonction explicites en francais).

**Ce qui n'est pas bien :**
- Seuils du ventilateur codes en dur (ligne 140) : `2000, 600, 35, 35, 75` sans constantes nommees.
- Pas de calibration du MQ-7 : R0=10.0 est la valeur par defaut, chaque capteur devrait etre calibre.
- Pas de log d'erreur HTTP : si `http.POST()` echoue (code != 201), aucune trace dans le moniteur serie.
- Reconnexion WiFi passive : uniquement verifiee en debut de [loop()](file:///home/mahdidou711/linux_data/Projets/iaq_project/esp32_iaq/esp32_iaq_v2.ino#104-162).

**Ce qui manque :**
- Pas de synchronisation NTP : horodatage attribue par le serveur, pas par l'ESP32.
- Pas de Deep Sleep : consommation permanente, incompatible batterie longue duree.
- Pas de stockage persistant SPIFFS/LittleFS : les 30 mesures buffrisees sont perdues si reset.
- Pas de mecanisme OTA (Over-The-Air) pour mettre a jour le firmware sans cable USB.

---

### 2.5 [requirements.txt](file:///home/mahdidou711/linux_data/Projets/iaq_project/requirements.txt) (1 ligne)

**Ce qui est bien :** Une seule dependance (`flask>=3.0,<4.0`). Installation minimale.

**Ce qui manque :** Pas de version pin exacte. `pip freeze > requirements.txt` serait plus reproductible.

---

### 2.6 [guide-header.tex](file:///home/mahdidou711/linux_data/Projets/iaq_project/guide-header.tex) (112 lignes)

**Ce qui est bien :**
- Page de couverture avec titre, sous-titre, separateur graphique.
- En-tetes et pieds de page avec numero de page.
- Sections colorees (primary, accent, warn) pour hierarchie visuelle.
- Code wrapping (`fvextra`) pour eviter le debordement des blocs de code.
- Police DejaVu Sans (bonne couverture Unicode).

**Ce qui n'est pas bien :** rien de notable.

---

### 2.7 [README.md](file:///home/mahdidou711/linux_data/Projets/iaq_project/README.md) (~250 lignes)

**Ce qui est bien :**
- Installation Windows 11 pas a pas (de Python jusqu'au navigateur).
- Tables de branchement pour chaque composant.
- Schema ASCII du diviseur de tension MQ-7.
- Liste de toutes les fonctions avec numeros de ligne.
- Liste de toutes les routes API avec methodes et descriptions.
- Limites connues documentees honnement.
- Roadmap detaillee (NTP, WebSocket, Telegram, Cloud, ADS1115).

**Ce qui manque :**
- Pas de `.gitignore` pour exclure `venv/`, [iaq.db](file:///home/mahdidou711/linux_data/Projets/iaq_project/iaq.db), `__pycache__/`.
- Pas de `LICENSE` pour clarifier les droits d'utilisation.

---

## 3. Securite electrique (schema EasyEDA vs code)

**Points positifs :**
- LM2596 : buck converter regulable, bien plus fiable qu'un regulateur lineaire.
- Diode D1 : protection inversion de polarite.
- MOSFET IRLZ44N : compatible logique 3.3V, bon choix pour le ventilateur.
- Transistor 2N2222 : pilotage correct du buzzer avec resistance de base 1K.

**Points critiques :**
- Diviseur de tension MQ-7 (R1=2.2K / R2=3.3K) : necessaire mais absent du schema EasyEDA.
- Pull-ups I2C : pas de resistances de rappel explicites sur SDA/SCL dans le schema.

**Elements du schema non utilises dans le code :**
- ADS1115 (ADC externe I2C) : present dans le schema, absent du code V2.
- MH-Z1911A (capteur CO2 NDIR) : present dans le schema, absent du code V2 et redondant avec le CCS811.

---

## 4. Captures d'ecran des tests navigateur

````carousel
![Dashboard en mode clair -- graphiques peuples, 5 capteurs "OK", badge 41 alertes](/home/mahdidou711/.gemini/antigravity/brain/2cd27101-8ab9-4415-aaf5-8cb43d7c0f93/dashboard_dark_mode_1774108663668.png)
<!-- slide -->
![Dashboard en mode sombre -- meme contenu, theme bascule correctement](/home/mahdidou711/.gemini/antigravity/brain/2cd27101-8ab9-4415-aaf5-8cb43d7c0f93/dashboard_light_mode_check_1774108673082.png)
<!-- slide -->
![Page "Comprendre les capteurs" -- 5 cartes educatives avec badges de seuil](/home/mahdidou711/.gemini/antigravity/brain/2cd27101-8ab9-4415-aaf5-8cb43d7c0f93/infos_page_1774108765947.png)
````

---

## 5. Verdict global

| Categorie            | Note   | Commentaire |
|:---------------------|:-------|:------------|
| Architecture         | 8/10   | Separation nette ESP32/Flask/JS. Protocole HTTP+JSON simple et universel. |
| Backend ([app.py](file:///home/mahdidou711/linux_data/Projets/iaq_project/app.py))   | 7.5/10 | Validation robuste, batch, export CSV. Manque auth et nettoyage periodique. |
| Dashboard ([index.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/index.html)) | 8.5/10 | Design pro, tolerant aux pannes, 3 onglets, zoom interactif. Polling a ameliorer. |
| Page educative ([infos.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/infos.html)) | 9/10 | Claire, pedagogique, coherente. Manque uniquement le toggle theme. |
| Firmware V2 ([esp32_iaq_v2.ino](file:///home/mahdidou711/linux_data/Projets/iaq_project/esp32_iaq/esp32_iaq_v2.ino)) | 8/10 | Watchdog, timeout I2C, buffer, ventilateur auto. Manque NTP et Deep Sleep. |
| Hardware / Schema    | 6/10   | LM2596 et MOSFET bien choisis. Diviseur et pull-ups absents du schema. |
| Documentation        | 9/10   | README exhaustif, PDF propre. Manque `.gitignore` et `LICENSE`. |
| Securite logicielle  | 4/10   | Routes destructrices ouvertes sans authentification. |

### Score global : 7.5 / 10

### Priorites a traiter

1. **Authentification API** : proteger `/api/clear`, `/api/seed`, et `/api/mesures` par un token ou une cle API.
2. **Schema EasyEDA** : ajouter le diviseur de tension MQ-7 et les pull-ups I2C au schema.
3. **Nettoyage periodique** : ajouter un `APScheduler` ou un thread pour [cleanup_old_data()](file:///home/mahdidou711/linux_data/Projets/iaq_project/app.py#104-112).
4. **`.gitignore` + `LICENSE`** : indispensables avant tout push sur GitHub.
5. **Toggle theme sur [infos.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/infos.html)** : coherence avec le dashboard.
