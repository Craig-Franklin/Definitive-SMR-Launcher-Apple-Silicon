"""Original launcher UI translations and private language/voice preferences.

Unknown source strings remain readable English. Map packages are never rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

from .activation import _assert_no_symlink_ancestor, _atomic_json

LANGUAGES = {"en": "English", "nl": "Nederlands", "fr": "Français", "de": "Deutsch",
             "hi": "हिन्दी", "it": "Italiano", "zh-Hans": "简体中文", "es": "Español"}
_LANGUAGE = "en"
# Columns are English, Dutch, French, German, Hindi, Italian, Mandarin, Spanish.
_ROWS = """
File|Bestand|Fichier|Datei|फ़ाइल|File|文件|Archivo
Help|Help|Aide|Hilfe|सहायता|Aiuto|帮助|Ayuda
Quit|Stoppen|Quitter|Beenden|बंद करें|Esci|退出|Salir
Map Library|Kaartenbibliotheek|Bibliothèque de cartes|Kartenbibliothek|मानचित्र लाइब्रेरी|Libreria mappe|地图库|Biblioteca de mapas
Collection|Verzameling|Collection|Sammlung|संग्रह|Raccolta|合集|Colección
Original Game|Origineel spel|Jeu original|Originalspiel|मूल गेम|Gioco originale|原版游戏|Juego original
Setup|Instellingen|Configuration|Einrichtung|सेटअप|Configurazione|设置|Configuración
Search maps|Kaarten zoeken|Rechercher des cartes|Karten suchen|मानचित्र खोजें|Cerca mappe|搜索地图|Buscar mapas
Play Selected|Selectie spelen|Jouer à la sélection|Auswahl spielen|चयनित खेलें|Gioca selezionata|游玩所选地图|Jugar selección
Import Map…|Kaart importeren…|Importer une carte…|Karte importieren…|मानचित्र आयात करें…|Importa mappa…|导入地图…|Importar mapa…
Choose Steam Library Folder…|Steam-bibliotheekmap kiezen…|Choisir le dossier de bibliothèque Steam…|Steam-Bibliotheksordner auswählen…|Steam लाइब्रेरी फ़ोल्डर चुनें…|Scegli cartella libreria Steam…|选择 Steam 库文件夹…|Elegir carpeta de biblioteca de Steam…
Installation & Help|Installatie en hulp|Installation et aide|Installation und Hilfe|इंस्टॉलेशन और सहायता|Installazione e aiuto|安装与帮助|Instalación y ayuda
Windows Feature Comparison|Windows-functies vergelijken|Comparaison des fonctions Windows|Vergleich mit Windows-Funktionen|Windows सुविधाओं की तुलना|Confronto funzionalità Windows|Windows 功能对比|Comparación de funciones de Windows
Install in Applications|Installeer in Apps|Installer dans Applications|In Programme installieren|Applications में इंस्टॉल करें|Installa in Applicazioni|安装到应用程序|Instalar en Aplicaciones
Steam Mac edition|Steam Mac-versie|Édition Mac Steam|Steam-Mac-Version|Steam Mac संस्करण|Edizione Mac Steam|Steam Mac 版|Edición Mac de Steam
Checking…|Controleren…|Vérification…|Wird geprüft…|जाँच हो रही है…|Verifica…|正在检查…|Comprobando…
Installed|Geïnstalleerd|Installé|Installiert|इंस्टॉल है|Installato|已安装|Instalado
Game not found|Spel niet gevonden|Jeu introuvable|Spiel nicht gefunden|गेम नहीं मिला|Gioco non trovato|未找到游戏|Juego no encontrado
Steam Mac game required|Steam Mac-spel vereist|Version Mac Steam requise|Steam-Mac-Spiel erforderlich|Steam Mac गेम आवश्यक है|Richiesta versione Mac Steam|需要 Steam Mac 版游戏|Se requiere el juego de Steam para Mac
Show|Tonen|Afficher|Anzeigen|दिखाएँ|Mostra|显示|Mostrar
All maps|Alle kaarten|Toutes les cartes|Alle Karten|सभी मानचित्र|Tutte le mappe|所有地图|Todos los mapas
Single player|Eén speler|Un joueur|Einzelspieler|एकल खिलाड़ी|Giocatore singolo|单人|Un jugador
Multiplayer|Meerdere spelers|Multijoueur|Mehrspieler|मल्टीप्लेयर|Multigiocatore|多人|Multijugador
Verified|Geverifieerd|Vérifié|Geprüft|सत्यापित|Verificata|已验证|Verificado
Not verified|Niet geverifieerd|Non vérifié|Nicht geprüft|सत्यापित नहीं|Non verificata|未验证|Sin verificar
Known issue|Bekend probleem|Problème connu|Bekanntes Problem|ज्ञात समस्या|Problema noto|已知问题|Problema conocido
Sort|Sorteren|Trier|Sortieren|क्रमबद्ध करें|Ordina|排序|Ordenar
Name A–Z|Naam A–Z|Nom A–Z|Name A–Z|नाम A–Z|Nome A–Z|名称 A–Z|Nombre A–Z
Created newest|Nieuwste aanmaakdatum|Création la plus récente|Neueste Erstellung|नवीनतम निर्माण|Creazione più recente|创建时间：最新|Creación más reciente
Created oldest|Oudste aanmaakdatum|Création la plus ancienne|Älteste Erstellung|सबसे पुराना निर्माण|Creazione meno recente|创建时间：最早|Creación más antigua
Updated newest|Nieuwste wijziging|Mise à jour la plus récente|Neueste Aktualisierung|नवीनतम अपडेट|Aggiornamento più recente|更新时间：最新|Actualización más reciente
Author|Auteur|Auteur|Autor|लेखक|Autore|作者|Autor
Map Details…|Kaartgegevens…|Détails de la carte…|Kartendetails…|मानचित्र विवरण…|Dettagli mappa…|地图详情…|Detalles del mapa…
Download & Import Selected|Selectie downloaden en importeren|Télécharger et importer la sélection|Auswahl herunterladen und importieren|चयनित डाउनलोड और आयात करें|Scarica e importa selezionate|下载并导入所选地图|Descargar e importar selección
Download & Import All…|Alles downloaden en importeren…|Tout télécharger et importer…|Alle herunterladen und importieren…|सभी डाउनलोड और आयात करें…|Scarica e importa tutte…|全部下载并导入…|Descargar e importar todo…
Refresh|Vernieuwen|Actualiser|Aktualisieren|ताज़ा करें|Aggiorna|刷新|Actualizar
Collection Source|Bron van verzameling|Source de la collection|Sammlungsquelle|संग्रह स्रोत|Fonte raccolta|合集来源|Origen de la colección
Map|Kaart|Carte|Karte|मानचित्र|Mappa|地图|Mapa
Download progress|Downloadvoortgang|Progression du téléchargement|Downloadfortschritt|डाउनलोड प्रगति|Avanzamento download|下载进度|Progreso de descarga
Archive size|Archiefgrootte|Taille de l’archive|Archivgröße|आर्काइव का आकार|Dimensione archivio|压缩包大小|Tamaño del archivo
Modified by|Gewijzigd door|Modifié par|Geändert von|संशोधक|Modificato da|修改者|Modificado por
Version|Versie|Version|Version|संस्करण|Versione|版本|Versión
Created|Gemaakt|Création|Erstellt|निर्माण|Creata|创建日期|Creado
Updated|Bijgewerkt|Mise à jour|Aktualisiert|अपडेट|Aggiornata|更新日期|Actualizado
Map type|Kaarttype|Type de carte|Kartentyp|मानचित्र प्रकार|Tipo di mappa|地图类型|Tipo de mapa
Archive source|Archiefbron|Source de l’archive|Archivquelle|आर्काइव स्रोत|Fonte archivio|压缩包来源|Origen del archivo
Archive file modified|Archiefbestand gewijzigd|Fichier d’archive modifié|Archivdatei geändert|आर्काइव फ़ाइल संशोधन|File archivio modificato|压缩包修改日期|Archivo modificado
Mac testing|Mac-tests|Tests Mac|Mac-Tests|Mac परीक्षण|Test Mac|Mac 测试|Pruebas en Mac
Not provided|Niet opgegeven|Non renseigné|Nicht angegeben|जानकारी उपलब्ध नहीं|Non indicato|未提供|No indicado
Author not provided|Auteur niet opgegeven|Auteur non renseigné|Autor nicht angegeben|लेखक उपलब्ध नहीं|Autore non indicato|未提供作者|Autor no indicado
date not provided|datum niet opgegeven|date non renseignée|Datum nicht angegeben|तारीख उपलब्ध नहीं|data non indicata|未提供日期|fecha no indicada
Open Archive Source|Archiefbron openen|Ouvrir la source de l’archive|Archivquelle öffnen|आर्काइव स्रोत खोलें|Apri fonte archivio|打开压缩包来源|Abrir origen del archivo
Author’s Source|Bron van auteur|Source de l’auteur|Quelle des Autors|लेखक का स्रोत|Fonte dell’autore|作者来源|Fuente del autor
Community Reviews & Rating|Beoordelingen van de gemeenschap|Avis et note de la communauté|Community-Bewertungen|समुदाय की समीक्षाएँ और रेटिंग|Recensioni e valutazione della comunità|社区评价与评分|Reseñas y valoración de la comunidad
Briefing|Briefing|Présentation|Einführung|परिदृश्य परिचय|Introduzione|任务简介|Introducción
Original mapInfo|Originele mapInfo|mapInfo d’origine|Originale mapInfo|मूल mapInfo|mapInfo originale|原始 mapInfo|mapInfo original
Mac Test & Identity|Mac-test en identiteit|Test Mac et identité|Mac-Test und Identität|Mac परीक्षण और पहचान|Test Mac e identità|Mac 测试与标识|Prueba Mac e identidad
Read Briefing Aloud|Briefing voorlezen|Lire la présentation à voix haute|Einführung vorlesen|परिचय सुनें|Leggi introduzione ad alta voce|朗读任务简介|Leer introducción en voz alta
Stop Reading|Voorlezen stoppen|Arrêter la lecture|Vorlesen beenden|पढ़ना रोकें|Interrompi lettura|停止朗读|Detener lectura
Close|Sluiten|Fermer|Schließen|बंद करें|Chiudi|关闭|Cerrar
Play Original Game|Origineel spel spelen|Jouer au jeu original|Originalspiel starten|मूल गेम खेलें|Gioca al gioco originale|游玩原版游戏|Jugar al juego original
Launcher installation|Launcher-installatie|Installation du lanceur|Launcher-Installation|लॉन्चर इंस्टॉलेशन|Installazione launcher|启动器安装|Instalación del lanzador
Installed in Applications.|Geïnstalleerd in Apps.|Installé dans Applications.|In Programme installiert.|Applications में इंस्टॉल है।|Installato in Applicazioni.|已安装到应用程序。|Instalado en Aplicaciones.
Clean game profile enrolled.|Schoon spelprofiel geregistreerd.|Profil de jeu propre enregistré.|Sauberes Spielprofil registriert.|स्वच्छ गेम प्रोफ़ाइल पंजीकृत है।|Profilo di gioco pulito registrato.|已登记干净的游戏配置。|Perfil limpio del juego registrado.
Set Up Clean Game|Schoon spel instellen|Configurer le jeu propre|Sauberes Spiel einrichten|स्वच्छ गेम सेट करें|Configura gioco pulito|设置干净的游戏配置|Configurar juego limpio
App Updates|App-updates|Mises à jour de l’app|App-Updates|ऐप अपडेट|Aggiornamenti app|应用更新|Actualizaciones de la app
Check for Updates|Zoeken naar updates|Rechercher des mises à jour|Nach Updates suchen|अपडेट जाँचें|Controlla aggiornamenti|检查更新|Buscar actualizaciones
Download & Install Update…|Update downloaden en installeren…|Télécharger et installer la mise à jour…|Update herunterladen und installieren…|अपडेट डाउनलोड और इंस्टॉल करें…|Scarica e installa aggiornamento…|下载并安装更新…|Descargar e instalar actualización…
Language|Taal|Langue|Sprache|भाषा|Lingua|语言|Idioma
Interface language|Interfacetaal|Langue de l’interface|Oberflächensprache|इंटरफ़ेस भाषा|Lingua dell’interfaccia|界面语言|Idioma de la interfaz
Voice|Stem|Voix|Stimme|आवाज़|Voce|声音|Voz
System default|Systeemstandaard|Valeur système|Systemstandard|सिस्टम डिफ़ॉल्ट|Predefinita di sistema|系统默认|Predeterminado del sistema
Refresh Voices|Stemmen vernieuwen|Actualiser les voix|Stimmen aktualisieren|आवाज़ें ताज़ा करें|Aggiorna voci|刷新声音列表|Actualizar voces
Manage Voices…|Stemmen beheren…|Gérer les voix…|Stimmen verwalten…|आवाज़ें प्रबंधित करें…|Gestisci voci…|管理声音…|Gestionar voces…
Translate Briefing|Briefing vertalen|Traduire la présentation|Einführung übersetzen|परिचय का अनुवाद करें|Traduci introduzione|翻译任务简介|Traducir introducción
Show Original|Origineel tonen|Afficher l’original|Original anzeigen|मूल दिखाएँ|Mostra originale|显示原文|Mostrar original
Translation Languages…|Vertaaltalen…|Langues de traduction…|Übersetzungssprachen…|अनुवाद की भाषाएँ…|Lingue di traduzione…|翻译语言…|Idiomas de traducción…
Translating…|Vertalen…|Traduction…|Wird übersetzt…|अनुवाद हो रहा है…|Traduzione…|正在翻译…|Traduciendo…
Custom Difficulty|Aangepaste moeilijkheid|Difficulté personnalisée|Eigener Schwierigkeitsgrad|कस्टम कठिनाई|Difficoltà personalizzata|自定义难度|Dificultad personalizada
Difficulty|Moeilijkheid|Difficulté|Schwierigkeitsgrad|कठिनाई|Difficoltà|难度|Dificultad
Map Editor|Kaarteditor|Éditeur de cartes|Karteneditor|मानचित्र संपादक|Editor di mappe|地图编辑器|Editor de mapas
Enable Map Editor|Kaarteditor inschakelen|Activer l’éditeur de cartes|Karteneditor aktivieren|मानचित्र संपादक सक्षम करें|Abilita editor mappe|启用地图编辑器|Activar editor de mapas
View Logs|Logboeken bekijken|Afficher les journaux|Protokolle anzeigen|लॉग देखें|Visualizza registri|查看日志|Ver registros
Save|Opslaan|Enregistrer|Speichern|सहेजें|Salva|保存|Guardar
Cancel|Annuleren|Annuler|Abbrechen|रद्द करें|Annulla|取消|Cancelar
Reset|Herstellen|Réinitialiser|Zurücksetzen|रीसेट करें|Ripristina|重置|Restablecer
Apply|Toepassen|Appliquer|Anwenden|लागू करें|Applica|应用|Aplicar
Activity|Activiteit|Activité|Aktivität|गतिविधि|Attività|活动|Actividad
Check Map Updates|Kaartupdates controleren|Vérifier les mises à jour des cartes|Kartenupdates prüfen|मानचित्र अपडेट जाँचें|Controlla aggiornamenti mappe|检查地图更新|Buscar actualizaciones de mapas
Refresh Rating|Beoordeling vernieuwen|Actualiser la note|Bewertung aktualisieren|रेटिंग ताज़ा करें|Aggiorna valutazione|刷新评分|Actualizar valoración
Community|Gemeenschap|Communauté|Community|समुदाय|Comunità|社区|Comunidad
Cancel Imports|Importeren annuleren|Annuler les importations|Importe abbrechen|आयात रद्द करें|Annulla importazioni|取消导入|Cancelar importaciones
Downloading|Downloaden|Téléchargement|Wird heruntergeladen|डाउनलोड हो रहा है|Download|正在下载|Descargando
Queued|In wachtrij|En attente|In Warteschlange|कतार में|In coda|排队中|En cola
Imported|Geïmporteerd|Importé|Importiert|आयातित|Importata|已导入|Importado
Ready|Gereed|Prêt|Bereit|तैयार|Pronta|就绪|Listo
Failed|Mislukt|Échec|Fehlgeschlagen|विफल|Non riuscito|失败|Error
Archive verified|Archief geverifieerd|Archive vérifiée|Archiv geprüft|आर्काइव सत्यापित|Archivio verificato|压缩包已验证|Archivo verificado
Needs inspection|Inspectie nodig|Vérification nécessaire|Prüfung erforderlich|जाँच आवश्यक है|Verifica necessaria|需要检查|Requiere revisión
Reported supported|Ondersteund volgens melding|Signalé comme compatible|Als unterstützt gemeldet|समर्थित बताया गया|Segnalata compatibile|据反馈支持|Compatible según informes
Reported unsupported|Niet ondersteund volgens melding|Signalé comme incompatible|Als nicht unterstützt gemeldet|असमर्थित बताया गया|Segnalata non compatibile|据反馈不支持|Incompatible según informes
Not reported|Niet gemeld|Non signalé|Nicht gemeldet|रिपोर्ट उपलब्ध नहीं|Non segnalato|暂无反馈|Sin informes
No briefing provided.|Geen briefing opgegeven.|Aucune présentation fournie.|Keine Einführung vorhanden.|परिचय उपलब्ध नहीं है।|Nessuna introduzione fornita.|未提供任务简介。|No hay introducción.
No mapInfo.txt provided.|Geen mapInfo.txt opgegeven.|Aucun mapInfo.txt fourni.|Keine mapInfo.txt vorhanden.|mapInfo.txt उपलब्ध नहीं है।|Nessun mapInfo.txt fornito.|未提供 mapInfo.txt。|No hay mapInfo.txt.
No matching maps.|Geen passende kaarten.|Aucune carte correspondante.|Keine passenden Karten.|कोई मेल खाता मानचित्र नहीं।|Nessuna mappa corrispondente.|没有匹配的地图。|No hay mapas coincidentes.
Import a map archive to start your library.|Importeer een kaartarchief om uw bibliotheek te starten.|Importez une archive de carte pour créer votre bibliothèque.|Importieren Sie ein Kartenarchiv für Ihre Bibliothek.|लाइब्रेरी शुरू करने के लिए मानचित्र आर्काइव आयात करें।|Importa un archivio mappa per iniziare la libreria.|导入地图压缩包以创建地图库。|Importe un archivo de mapa para iniciar su biblioteca.
Choose a map to see its scenario and source details.|Kies een kaart voor scenario- en brongegevens.|Choisissez une carte pour voir son scénario et sa source.|Wählen Sie eine Karte für Szenario- und Quelldetails.|परिदृश्य और स्रोत विवरण के लिए मानचित्र चुनें।|Scegli una mappa per vedere scenario e fonte.|选择地图以查看场景和来源详情。|Elija un mapa para ver el escenario y el origen.
Choose a map to see its archive identity.|Kies een kaart om het archief te identificeren.|Choisissez une carte pour identifier son archive.|Wählen Sie eine Karte für ihre Archivkennung.|आर्काइव की पहचान देखने के लिए मानचित्र चुनें।|Scegli una mappa per identificare il suo archivio.|选择地图以查看压缩包标识。|Elija un mapa para ver la identidad del archivo.
Keep the launcher in Applications for easy access and updates.|Bewaar de launcher in Apps voor eenvoudige toegang en updates.|Gardez le lanceur dans Applications pour faciliter l’accès et les mises à jour.|Speichern Sie den Launcher in Programme für einfachen Zugriff und Updates.|आसान पहुँच और अपडेट के लिए लॉन्चर को Applications में रखें।|Conserva il launcher in Applicazioni per accesso e aggiornamenti semplici.|将启动器保存在应用程序中，方便访问和更新。|Guarde el lanzador en Aplicaciones para facilitar el acceso y las actualizaciones.
Choose a steamapps folder, not Steam.app.|Kies een steamapps-map, niet Steam.app.|Choisissez un dossier steamapps, pas Steam.app.|Wählen Sie einen steamapps-Ordner, nicht Steam.app.|steamapps फ़ोल्डर चुनें, Steam.app नहीं।|Scegli una cartella steamapps, non Steam.app.|请选择 steamapps 文件夹，而非 Steam.app。|Elija una carpeta steamapps, no Steam.app.
Created and updated dates are declared by the map package; archive dates describe the download file.|Aanmaak- en wijzigingsdatums komen uit het kaartpakket; archiefdatums horen bij het downloadbestand.|Les dates de création et de mise à jour viennent du paquet de carte ; les dates d’archive concernent le fichier téléchargé.|Erstellungs- und Änderungsdaten stammen aus dem Kartenpaket; Archivdaten beziehen sich auf die heruntergeladene Datei.|निर्माण और अपडेट की तारीखें मानचित्र पैकेज से आती हैं; आर्काइव की तारीखें डाउनलोड फ़ाइल की हैं।|Le date di creazione e aggiornamento provengono dal pacchetto mappa; quelle dell’archivio descrivono il file scaricato.|创建和更新日期由地图包提供；压缩包日期描述下载文件。|Las fechas de creación y actualización proceden del paquete del mapa; las del archivo describen la descarga.
Play the stock Steam profile with its own saves. Your custom maps and their saves stay in separate profiles.|Speel het originele Steam-profiel met zijn eigen opgeslagen spellen. Uw aangepaste kaarten en hun opgeslagen spellen blijven in aparte profielen.|Jouez avec le profil Steam d’origine et ses sauvegardes. Vos cartes personnalisées et leurs sauvegardes restent dans des profils distincts.|Spielen Sie das originale Steam-Profil mit eigenen Spielständen. Eigene Karten und deren Spielstände bleiben in getrennten Profilen.|मूल Steam प्रोफ़ाइल को उसके अपने सेव के साथ खेलें। कस्टम मानचित्र और उनके सेव अलग प्रोफ़ाइल में रहते हैं।|Gioca con il profilo Steam originale e i suoi salvataggi. Le mappe personalizzate e i relativi salvataggi restano in profili separati.|使用原版 Steam 配置及其存档游玩。自定义地图和存档保存在各自独立的配置中。|Juegue con el perfil original de Steam y sus partidas. Los mapas personalizados y sus partidas permanecen en perfiles separados.
The launcher preserves each source archive and keeps future saves with the selected map.|De launcher bewaart elk bronarchief en houdt toekomstige opgeslagen spellen bij de gekozen kaart.|Le lanceur conserve chaque archive source et les futures sauvegardes avec la carte choisie.|Der Launcher bewahrt jedes Quellarchiv und speichert künftige Spielstände bei der gewählten Karte.|लॉन्चर हर स्रोत आर्काइव को सुरक्षित रखता है और भविष्य के सेव चयनित मानचित्र के साथ रखता है।|Il launcher conserva ogni archivio originale e i futuri salvataggi con la mappa selezionata.|启动器保留每个原始压缩包，并将今后的存档与所选地图一起保存。|El lanzador conserva cada archivo original y guarda las futuras partidas con el mapa seleccionado.
Automatically install verified updates when I quit the launcher|Geverifieerde updates automatisch installeren bij afsluiten|Installer automatiquement les mises à jour vérifiées à la fermeture du lanceur|Geprüfte Updates beim Beenden des Launchers automatisch installieren|लॉन्चर बंद करने पर सत्यापित अपडेट अपने आप इंस्टॉल करें|Installa automaticamente gli aggiornamenti verificati alla chiusura del launcher|退出启动器时自动安装已验证的更新|Instalar actualizaciones verificadas automáticamente al salir del lanzador
Install a verified copy in your Applications folder. Your maps and saves stay in your library.|Installeer een geverifieerde kopie in uw Apps-map. Uw kaarten en opgeslagen spellen blijven in uw bibliotheek.|Installez une copie vérifiée dans votre dossier Applications. Vos cartes et sauvegardes restent dans votre bibliothèque.|Installieren Sie eine geprüfte Kopie in Ihrem Programme-Ordner. Karten und Spielstände bleiben in Ihrer Bibliothek.|अपने Applications फ़ोल्डर में सत्यापित कॉपी इंस्टॉल करें। मानचित्र और सेव लाइब्रेरी में बने रहते हैं।|Installa una copia verificata nella cartella Applicazioni. Mappe e salvataggi restano nella libreria.|在应用程序文件夹中安装已验证的副本。地图和存档仍保存在地图库中。|Instale una copia verificada en su carpeta Aplicaciones. Los mapas y partidas permanecen en la biblioteca.
Development build. The signed release provides the Install in Applications button.|Ontwikkelversie. De ondertekende release heeft de knop Installeer in Apps.|Version de développement. La version signée propose le bouton Installer dans Applications.|Entwicklungsversion. Die signierte Veröffentlichung bietet In Programme installieren.|डेवलपमेंट बिल्ड। हस्ताक्षरित रिलीज़ में Applications में इंस्टॉल करें बटन मिलता है।|Versione di sviluppo. La versione firmata offre il pulsante Installa in Applicazioni.|开发版本。签名发布版提供“安装到应用程序”按钮。|Versión de desarrollo. La versión firmada incluye el botón Instalar en Aplicaciones.
Language & Voice|Taal en stem|Langue et voix|Sprache und Stimme|भाषा और आवाज़|Lingua e voce|语言与声音|Idioma y voz
From|Van|De|Von|से|Da|源语言|De
To|Naar|Vers|Nach|में|A|目标语言|A
Experimental Editions…|Experimentele edities…|Éditions expérimentales…|Experimentelle Ausgaben…|प्रायोगिक संस्करण…|Edizioni sperimentali…|实验版本…|Ediciones experimentales…
Enable terrain editor|Terreineditor inschakelen|Activer l’éditeur de terrain|Geländeeditor aktivieren|भूभाग संपादक सक्षम करें|Abilita editor del terreno|启用地形编辑器|Activar editor de terreno
Use Windows launcher custom difficulty levels|Aangepaste moeilijkheidsniveaus van de Windows-launcher gebruiken|Utiliser les difficultés personnalisées du lanceur Windows|Eigene Schwierigkeitsstufen des Windows-Launchers verwenden|Windows लॉन्चर के कस्टम कठिनाई स्तर इस्तेमाल करें|Usa le difficoltà personalizzate del launcher Windows|使用 Windows 启动器的自定义难度|Usar las dificultades personalizadas del lanzador de Windows
Create Experimental Edition|Experimentele editie maken|Créer une édition expérimentale|Experimentelle Ausgabe erstellen|प्रायोगिक संस्करण बनाएँ|Crea edizione sperimentale|创建实验版本|Crear edición experimental
Import Selected Updates|Geselecteerde updates importeren|Importer les mises à jour sélectionnées|Ausgewählte Updates importieren|चयनित अपडेट आयात करें|Importa aggiornamenti selezionati|导入所选更新|Importar actualizaciones seleccionadas
New version|Nieuwe versie|Nouvelle version|Neue Version|नया संस्करण|Nuova versione|新版本|Nueva versión
Revised archive|Herzien archief|Archive révisée|Überarbeitetes Archiv|संशोधित आर्काइव|Archivio aggiornato|修订压缩包|Archivo revisado
Installed map|Geïnstalleerde kaart|Carte installée|Installierte Karte|इंस्टॉल मानचित्र|Mappa installata|已安装地图|Mapa instalado
Available update|Beschikbare update|Mise à jour disponible|Verfügbares Update|उपलब्ध अपडेट|Aggiornamento disponibile|可用更新|Actualización disponible
Type|Type|Type|Typ|प्रकार|Tipo|类型|Tipo
Recent launcher operations, retained locally between sessions.|Recente launcher-activiteiten, lokaal bewaard tussen sessies.|Opérations récentes du lanceur, conservées localement entre les sessions.|Letzte Launcher-Aktionen, lokal zwischen Sitzungen gespeichert.|हाल की लॉन्चर गतिविधियाँ, सत्रों के बीच स्थानीय रूप से सुरक्षित।|Operazioni recenti del launcher, conservate localmente tra le sessioni.|近期启动器操作，在会话之间保存在本机。|Operaciones recientes del lanzador, guardadas localmente entre sesiones.
Briefing translation uses Apple’s installed language models on macOS 26 or later. Original map text is preserved.|Briefingvertaling gebruikt geïnstalleerde Apple-taalmodellen op macOS 26 of nieuwer. Originele kaarttekst blijft behouden.|La traduction utilise les modèles de langue Apple installés sur macOS 26 ou ultérieur. Le texte original est conservé.|Die Übersetzung verwendet installierte Apple-Sprachmodelle ab macOS 26. Der Originaltext bleibt erhalten.|परिचय का अनुवाद macOS 26 या बाद के संस्करण पर इंस्टॉल Apple भाषा मॉडल से होता है। मूल पाठ सुरक्षित रहता है।|La traduzione usa i modelli linguistici Apple installati su macOS 26 o successivo. Il testo originale è conservato.|任务简介翻译使用 macOS 26 或更高版本中已安装的 Apple 语言模型。地图原文保持不变。|La traducción usa modelos de idioma de Apple instalados en macOS 26 o posterior. El texto original se conserva.
Updates are imported as separate editions. Existing maps and saves remain available.|Updates worden als aparte edities geïmporteerd. Bestaande kaarten en opgeslagen spellen blijven beschikbaar.|Les mises à jour sont importées séparément. Les cartes et sauvegardes existantes restent disponibles.|Updates werden als getrennte Ausgaben importiert. Vorhandene Karten und Spielstände bleiben verfügbar.|अपडेट अलग संस्करणों के रूप में आयात होते हैं। मौजूदा मानचित्र और सेव उपलब्ध रहते हैं।|Gli aggiornamenti vengono importati come edizioni separate. Mappe e salvataggi esistenti restano disponibili.|更新会导入为独立版本。现有地图和存档仍然可用。|Las actualizaciones se importan como ediciones independientes. Los mapas y partidas existentes siguen disponibles.
Experimental on the Mac edition. The new edition starts with empty saves and does not replace your original. Test a fresh scenario first. Custom difficulty is refused if the map supplies its own definitions.|Experimenteel op de Mac-versie. De nieuwe editie begint zonder opgeslagen spellen en vervangt het origineel niet. Test eerst een nieuw scenario. Aangepaste moeilijkheid is geblokkeerd als de kaart eigen definities bevat.|Expérimental sur Mac. La nouvelle édition démarre sans sauvegardes et conserve l’original. Testez d’abord un nouveau scénario. La difficulté personnalisée est refusée si la carte fournit ses propres définitions.|Auf dem Mac experimentell. Die neue Ausgabe beginnt ohne Spielstände und ersetzt das Original nicht. Testen Sie zuerst ein neues Szenario. Eigene Schwierigkeitsstufen werden abgelehnt, wenn die Karte eigene Definitionen enthält.|Mac संस्करण पर प्रायोगिक है। नया संस्करण बिना सेव शुरू होता है और मूल को नहीं बदलता। पहले नया परिदृश्य जाँचें। यदि मानचित्र अपनी परिभाषाएँ देता है तो कस्टम कठिनाई अस्वीकार होती है।|Sperimentale su Mac. La nuova edizione inizia senza salvataggi e non sostituisce l’originale. Prova prima un nuovo scenario. La difficoltà personalizzata è rifiutata se la mappa fornisce le proprie definizioni.|此功能在 Mac 版中属实验性质。新版本从空存档开始，不会替换原版。请先测试全新场景。如果地图提供了自己的难度定义，则不允许使用自定义难度。|Experimental en Mac. La nueva edición empieza sin partidas y conserva el original. Pruebe primero un escenario nuevo. Se rechaza la dificultad personalizada si el mapa incluye sus propias definiciones.
Map Updates|Kaartupdates|Mises à jour des cartes|Kartenupdates|मानचित्र अपडेट|Aggiornamenti mappe|地图更新|Actualizaciones de mapas
Installed archive|Geïnstalleerd archief|Archive installée|Installiertes Archiv|इंस्टॉल आर्काइव|Archivio installato|已安装压缩包|Archivo instalado
Available archive|Beschikbaar archief|Archive disponible|Verfügbares Archiv|उपलब्ध आर्काइव|Archivio disponibile|可用压缩包|Archivo disponible
Change|Wijziging|Changement|Änderung|बदलाव|Modifica|变更|Cambio
Experimental Editions|Experimentele edities|Éditions expérimentales|Experimentelle Ausgaben|प्रायोगिक संस्करण|Edizioni sperimentali|实验版本|Ediciones experimentales
No matching activity yet.|Nog geen passende activiteit.|Aucune activité correspondante.|Noch keine passende Aktivität.|अभी कोई मेल खाती गतिविधि नहीं।|Nessuna attività corrispondente.|暂无匹配的活动。|Aún no hay actividad coincidente.
No update check yet.|Nog niet op updates gecontroleerd.|Aucune vérification effectuée.|Noch keine Update-Prüfung.|अभी अपडेट की जाँच नहीं हुई।|Nessun controllo aggiornamenti.|尚未检查更新。|Aún no se han buscado actualizaciones.
No newer stable release is available.|Geen nieuwere stabiele release beschikbaar.|Aucune version stable plus récente.|Keine neuere stabile Version verfügbar.|कोई नया स्थिर संस्करण उपलब्ध नहीं है।|Nessuna versione stabile più recente.|没有更新的稳定版本。|No hay una versión estable más reciente.
No local gameplay result recorded.|Geen lokaal spelresultaat vastgelegd.|Aucun résultat de jeu local enregistré.|Kein lokales Spielergebnis erfasst.|कोई स्थानीय गेमप्ले परिणाम दर्ज नहीं है।|Nessun risultato di gioco locale registrato.|尚未记录本地游玩结果。|No hay resultados de juego locales registrados.
Original Game is ready.|Origineel spel is gereed.|Le jeu original est prêt.|Das Originalspiel ist bereit.|मूल गेम तैयार है।|Il gioco originale è pronto.|原版游戏已就绪。|El juego original está listo.
Railroads launched. Quit it before changing maps.|Railroads gestart. Sluit het af voordat u kaarten wisselt.|Railroads est lancé. Quittez-le avant de changer de carte.|Railroads gestartet. Beenden Sie es vor einem Kartenwechsel.|Railroads शुरू हो गया है। मानचित्र बदलने से पहले इसे बंद करें।|Railroads avviato. Chiudilo prima di cambiare mappa.|Railroads 已启动。更换地图前请退出游戏。|Railroads iniciado. Ciérrelo antes de cambiar de mapa.
Enable terrain editor — export workflow pending|Terreineditor inschakelen — export nog niet gereed|Activer l’éditeur de terrain — export en préparation|Geländeeditor aktivieren — Export noch ausstehend|भूभाग संपादक सक्षम करें — निर्यात प्रक्रिया लंबित|Abilita editor del terreno — esportazione in preparazione|启用地形编辑器 — 导出流程待完成|Activar editor de terreno — exportación pendiente
No newer matching versions found.|Geen nieuwere passende versies gevonden.|Aucune version correspondante plus récente.|Keine neueren passenden Versionen gefunden.|कोई नया मेल खाता संस्करण नहीं मिला।|Nessuna versione corrispondente più recente.|未找到匹配的新版本。|No se encontraron versiones nuevas coincidentes.
Select the updates to import.|Selecteer updates om te importeren.|Sélectionnez les mises à jour à importer.|Wählen Sie die zu importierenden Updates.|आयात के लिए अपडेट चुनें।|Seleziona gli aggiornamenti da importare.|选择要导入的更新。|Seleccione las actualizaciones que desea importar.
"""
TRANSLATIONS = {code: {} for code in LANGUAGES}
for _row in _ROWS.strip().splitlines():
    _values = _row.split("|")
    if len(_values) != len(LANGUAGES):
        raise RuntimeError("Invalid launcher translation row")
    for _code, _value in zip(LANGUAGES, _values):
        TRANSLATIONS[_code][_values[0]] = _value

_EDITOR_NOTICE = ['Editor saving currently conflicts with protected map assets; that option stays unavailable until edited maps can be captured safely.\n\nCustom difficulty is experimental on the Mac edition. The new edition starts with empty saves and does not replace your original. Test a fresh scenario first. Custom difficulty is refused if the map supplies its own definitions.', 'Opslaan in de editor botst met beveiligde kaartbestanden; deze optie blijft uitgeschakeld totdat bewerkte kaarten veilig kunnen worden vastgelegd.\n\nAangepaste moeilijkheid is experimenteel op Mac. De nieuwe editie begint zonder opgeslagen spellen en vervangt het origineel niet. Test eerst een nieuw scenario. Aangepaste moeilijkheid is geblokkeerd als de kaart eigen definities bevat.', 'L’enregistrement dans l’éditeur est incompatible avec la protection des fichiers de carte ; cette option reste indisponible tant que les cartes modifiées ne peuvent pas être conservées en sécurité.\n\nLa difficulté personnalisée est expérimentale sur Mac. La nouvelle édition démarre sans sauvegardes et conserve l’original. Testez d’abord un nouveau scénario. Elle est refusée si la carte fournit ses propres définitions.', 'Das Speichern im Editor widerspricht dem Schutz der Kartendateien; diese Option bleibt gesperrt, bis bearbeitete Karten sicher übernommen werden können.\n\nEigene Schwierigkeitsstufen sind auf dem Mac experimentell. Die neue Ausgabe beginnt ohne Spielstände und ersetzt das Original nicht. Testen Sie zuerst ein neues Szenario. Eigene Stufen werden abgelehnt, wenn die Karte eigene Definitionen enthält.', 'संपादक में सहेजना सुरक्षित मानचित्र फ़ाइलों से टकराता है; संपादित मानचित्र सुरक्षित रूप से सहेजे जा सकने तक यह विकल्प उपलब्ध नहीं है।\n\nMac पर कस्टम कठिनाई प्रायोगिक है। नया संस्करण बिना सेव शुरू होता है और मूल को नहीं बदलता। पहले नया परिदृश्य जाँचें। मानचित्र की अपनी परिभाषाएँ होने पर कस्टम कठिनाई अस्वीकार होती है।', 'Il salvataggio nell’editor è incompatibile con la protezione dei file della mappa; l’opzione resta indisponibile finché le mappe modificate non possono essere conservate in sicurezza.\n\nLa difficoltà personalizzata è sperimentale su Mac. La nuova edizione inizia senza salvataggi e conserva l’originale. Prova prima un nuovo scenario. È rifiutata se la mappa fornisce le proprie definizioni.', '编辑器保存功能目前与受保护的地图文件冲突；在能够安全保存编辑后的地图之前，此选项暂不可用。\n\n自定义难度在 Mac 版中属实验性质。新版本从空存档开始，不会替换原版。请先测试全新场景。如果地图提供了自己的难度定义，则不允许使用自定义难度。', 'Guardar en el editor entra en conflicto con la protección de los archivos del mapa; la opción sigue desactivada hasta poder conservar los mapas editados de forma segura.\n\nLa dificultad personalizada es experimental en Mac. La nueva edición empieza sin partidas y conserva el original. Pruebe primero un escenario nuevo. Se rechaza si el mapa incluye sus propias definiciones.']
for _code, _value in zip(LANGUAGES, _EDITOR_NOTICE):
    TRANSLATIONS[_code][_EDITOR_NOTICE[0]] = _value


def set_language(code: str) -> None:
    global _LANGUAGE
    if code not in LANGUAGES:
        raise ValueError("Unsupported interface language")
    _LANGUAGE = code


def tr(text: str, **values) -> str:
    translated = TRANSLATIONS[_LANGUAGE].get(text, text)
    return translated.format(**values) if values else translated


@dataclass
class LanguagePreferences:
    library: Path
    language: str = "en"
    voice: str = ""

    @property
    def path(self) -> Path:
        return Path(self.library).expanduser().absolute() / "language-voice.json"

    def load(self) -> "LanguagePreferences":
        _assert_no_symlink_ancestor(self.path)
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema") != 1:
                raise ValueError("Invalid language preferences")
            language, voice = value.get("language", "en"), value.get("voice", "")
            if not isinstance(language, str) or language not in LANGUAGES or not isinstance(voice, str) or len(voice) > 256:
                raise ValueError("Invalid language or voice preference")
            self.language, self.voice = language, voice
        return self

    def save(self, language: str, voice: str = "") -> None:
        if not isinstance(language, str) or language not in LANGUAGES or not isinstance(voice, str) or len(voice) > 256:
            raise ValueError("Invalid language or voice preference")
        _assert_no_symlink_ancestor(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _assert_no_symlink_ancestor(self.path)
        _atomic_json(self.path, {"schema": 1, "language": language, "voice": voice})
        self.path.chmod(0o600)
        self.language, self.voice = language, voice


class TranslationError(RuntimeError):
    pass


def translation_helper() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "smr-translate"
    return Path(__file__).resolve().parents[2] / "build" / "smr-translate"


def translate_briefing(text: str, target: str, source: str = "en") -> str:
    """Call the bundled Apple provider from a worker thread; never alter map data."""
    if target not in LANGUAGES or source not in LANGUAGES:
        raise TranslationError("Choose a supported translation language.")
    if not text.strip() or source == target:
        return text
    if len(text.encode("utf-8")) > 500_000:
        raise TranslationError("This briefing is too large to translate.")
    helper = translation_helper()
    if not helper.is_file():
        raise TranslationError("Apple translation is unavailable in this build. Use a release with the Apple translation helper on macOS 26 or later.")
    try:
        result = subprocess.run([str(helper)], input=json.dumps({"text": text, "source": source, "target": target}),
                                capture_output=True, text=True, timeout=180)
        response = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise TranslationError("Apple translation could not complete. Check installed Translation Languages in System Settings.") from exc
    if not isinstance(response, dict):
        raise TranslationError("Invalid response from Apple translation.")
    if result.returncode or response.get("error"):
        raise TranslationError(str(response.get("error", "Apple translation could not complete.")))
    translated = response.get("text")
    if not isinstance(translated, str) or not translated.strip():
        raise TranslationError("Apple translation returned no text.")
    return translated


def open_translation_settings() -> None:
    try:
        result = subprocess.run(["/usr/bin/open", "x-apple.systempreferences:com.apple.Localization-Settings.extension"],
                                capture_output=True, timeout=15)
        if result.returncode:
            subprocess.run(["/usr/bin/open", "-a", "System Settings"], check=True,
                           capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TranslationError("Open System Settings → General → Language & Region → Translation Languages.") from exc
