# HALIDA - RAW negative inversion tool for Linux



![banner](img/banner.gif)

## Table of content

* [**Introduction**](#introduction)  
* [**Sample photos**](#sample-photos)
* [**Prerequisites**](#prerequisites)
* [**Features and how to use Halida**](#how-to-use-halida)  
 --- [Add folders](#add-entire-folders)  
 --- [Crop](#crop)  
 --- [Develop](#develop)  
 --- [List/grid view](#show-files-in-list-or-grid-view)  
 --- [RAW-file support](#supports-raw-files)  
 --- [Rotate, crop, develop](#rotate-crop-and-develop)  
 
## Introduction

It's important for me to point out that this whole project is entirely vibe coded using the free version of ChatGPT. I've barely written a single line of the code myself. It's clunky, the code is bulky and it's definitely slow. 

To cut to the chase, I'm not a programmer. I just ended up finding Sygnynts script for inverting negatives last week and figured a GUI and the ability to process something other than 16-bit TIFF files would make my life a lot easier. You can find his project [here](https://github.com/Signynt/signynts-darkroom-script). It's amazing.

I'm not a programmer and the code is shit. I just wanted a GUI and the ability to import my RAW files straight away from my camera. All credits go to [Signynt](https://github.com/Signynt/signynts-darkroom-script).

## Prerequisites

Running this script requires ImageMagick to be installed. It's not required for the actual script to run, it's only used for importing RAW files and converting them into TIF-files.

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

