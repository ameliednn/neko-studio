# Neko Studio

Saisissez un lieu : Neko Studio cherche des vidéos et photos réelles de ce lieu, vous les fait valider, puis monte un Reel vertical 1080 × 1920 de 15, 20 ou 30 secondes, téléchargeable en MP4. Il n'ajoute ni texte, ni voix, ni visage, et n'utilise aucune image générée par IA.

## Installation (une seule fois, sans coder)

1. **Python** : ouvrez le menu Démarrer, tapez « PowerShell », ouvrez-le et collez :
   `winget install Python.Python.3.12`
2. **FFmpeg** (le moteur vidéo), dans la même fenêtre :
   `winget install Gyan.FFmpeg`
   Fermez ensuite PowerShell.
3. **Clé Pexels (facultative mais recommandée)** :
   - créez un compte gratuit sur https://www.pexels.com/api/ et copiez votre clé ;
   - dans le dossier Neko Studio, faites une copie de `.env.example` et renommez-la `.env` ;
   - ouvrez `.env` avec le Bloc-notes et collez la clé après `PEXELS_API_KEY=`, puis enregistrez.
   La clé reste sur votre ordinateur : elle n'apparaît ni dans le code, ni dans l'interface.
   Sans clé, Neko Studio utilise seulement Wikimedia Commons.

## Utilisation

1. Double-cliquez sur **Lancer Neko Studio.bat**. Le navigateur s'ouvre sur http://localhost:8501 (la première fois, l'installation des composants prend une minute).
2. Tapez une destination, par exemple `Shirakawa-go, Japon`, puis **Rechercher les médias**.
3. Regardez la grille : les médias « Fiables » (le nom du lieu figure dans leur titre) sont pré-cochés. Décochez ce qui ne convient pas ; ne cochez un média « À vérifier » que si vous avez vérifié qu'il montre bien le lieu.
4. Choisissez 15, 20 ou 30 s, puis **Générer le Reel** (environ 1 minute).
5. Regardez l'aperçu, puis **Télécharger le MP4**. Téléchargez aussi **Crédits à citer** : les photos sous licence CC BY ou CC BY-SA exigent de nommer l'auteur dans la légende Instagram.

Chaque Reel est aussi enregistré dans `outputs/`, avec `sources.json` (sources et licences de chaque média).

## Utiliser le studio depuis un téléphone

Par défaut, seul cet ordinateur peut ouvrir le studio. Pour y accéder depuis un téléphone connecté au même Wi-Fi : mettez un mot de passe dans `.env` (`NEKO_PASSWORD=...`), remplacez `address = "localhost"` par `address = "0.0.0.0"` dans `.streamlit/config.toml`, relancez, puis ouvrez `http://<adresse-de-l-ordinateur>:8501` sur le téléphone.

## Limites à connaître

- « Fiable » signifie que le nom exact du lieu figure dans le titre du média, pas qu'il a été géolocalisé. Relisez la grille avant de publier.
- Les lieux peu connus (un petit restaurant, un sentier) ont souvent trop peu de médias : le studio le dit et refuse de générer en dessous de 4 médias.
- Les vidéos ou photos peuvent montrer des personnes, des logos ou des œuvres protégées : vérifiez avant tout usage commercial.

## Pour les développeurs

- Ajouter une source : un fichier dans `neko/sources/` (classe `MediaSource`) et une ligne dans `neko/sources/__init__.py`.
- Ajouter votre visage et votre voix plus tard : une classe `Layer` dans `neko/layers.py`, passée à `generate(..., layers=[...])`.
