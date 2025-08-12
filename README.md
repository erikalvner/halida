# HALIDA

![banner](img/banner.png)

## Table of content

* [Installation](#installation)
* [Sample photos](#sample-photos)  
* [How to use](#how-to-use-halida)  
 --- [Add folders](#add-entire-folders)  
 --- [Crop](#crop)  
 --- [Develop](#develop)  
 --- [List/grid view](#show-files-in-list-or-grid-view)  
 --- [RAW-file support](#supports-raw-files)  
 --- [Rotate, crop, develop](#rotate-crop-and-develop)  
 

## Installation

Installing Halida is as simple as pasting the following into your terminal of choice:

`` curl -fssl [placeholder] https://github.com/erikalvner/halida/releases/download/v0.1/install.sh ``

Would you rather not run a random script off the internet, you can just as easily follow the steps below:

1. Download the latest release of the AppImage [here](www.app.image). 
2. Go to its path and run `` sudo chmod +x Halida.AppImage ``. 
3. You can now run the AppImage by locating it in the terminal and simply writing `` ./Halida.AppImage ``.

Make a .desktop entry **Optional**

4. Run `` nano ~/.local/bin/halida.desktop ``
5. Paste the following:  
`` [Desktop Entry]  
Name=Halida  
Comment=Halida Negative Inverter  
Exec=Halida.AppImage  
Terminal=false  
Type=Application  
Icon=halida  
StartupNotify=true  
Categories=Graphical;Photography;  
Keywords=photography; ``


## Sample photos

![Gustav](sample-photos/gustav-scan.jpg)

![Gustav](sample-photos/gustav-converted.jpg)

![Jacob](sample-photos/jacob-scan.jpg)

![Jacob](sample-photos/jacob-converted.jpg)


## How to use Halida

### Add entire folders

![folder](img/gif/addfolder.gif)

### Crop

![crop](img/gif/crop.gif)

### Develop

![develop](img/gif/develop.gif)

### Show files in list or grid

![list or grid view](img/gif/listgrid.gif)

### Supports RAW files

Halida supports .cr2, .nef, .arw, .dng, .rw2, .orf, and .raf files as well as TIFF. Upon import, all RAW files are converted into 16-bit TIFF files.

![raw](img/gif/rawfiles.gif)

### Rotate, crop and develop

![rotate, crop, develop](img/gif/rotcrodev.gif)

