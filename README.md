# TeX's Music Mezcla - Creative Vocal Blender

TeX's Music Mezcla is a standalone audio mixing application that allows you to blend multiple vocal tracks with an instrumental track directly in your browser. All audio processing happens client-side using the Web Audio API, so no backend or internet connection is required after the initial load.

## Features

- Blend multiple vocal tracks together with a crossfader
- Apply various audio effects (reverb, delay, distortion, tremolo)
- Auto-pilot modes for automatic blending
- Real-time audio-reactive visualization
- Master volume controls for all tracks

## Run Locally

### Method 1: Using the run.bat file (Windows)

Simply double-click the `run.bat` file in the project folder. This will:
1. Install dependencies if needed
2. Build the application
3. Start a local server
4. The application will be available at http://localhost:4173

### Method 2: Manual setup

**Prerequisites:** Node.js

1. Install dependencies:
   ```bash
   npm install
   ```

2. Build the application:
   ```bash
   npm run build
   ```

3. Start the preview server:
   ```bash
   npm run preview
   ```

4. Open your browser and navigate to http://localhost:4173

## How to Use

1. Load multiple vocal tracks (must have the same timing/lyrics)
2. Load one instrumental track
3. Adjust the crossfader to blend between vocal tracks
4. Apply effects to individual tracks
5. Use autopilot modes for automatic blending
6. Adjust master volume controls as needed

## Note

This is a completely standalone application. All audio processing happens in your browser - no internet connection or API keys required after the initial page load.
