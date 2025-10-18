import sys
import threading
import numpy as np
from pydub import AudioSegment
import pyaudio
import math
import librosa # Using librosa for high-quality pitch shifting
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton,
                             QSlider, QLabel, QFileDialog, QListWidget, QListWidgetItem,
                             QHBoxLayout, QGroupBox)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QPainter, QColor, QBrush, QPen
from PySide6.QtCore import QPointF

# --- Configuration ---
CHUNK_SIZE = 1024
TARGET_SAMPLE_RATE = 44100
TARGET_CHANNELS = 2
TARGET_SAMPLE_WIDTH = 2

# --- Custom Slider for Visualizing Vocal Positions ---
class AudioSlider(QWidget):
    sliderMoved = Signal(float)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(50)
        self.slider_position = 0.0
        self.vocal_positions = []
    def set_vocal_positions(self, positions):
        self.vocal_positions = positions
        self.update()
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track_y = self.height() // 2
        painter.setPen(QPen(QColor(120, 120, 120), 2))
        painter.drawLine(10, track_y, self.width() - 10, track_y)
        for pos in self.vocal_positions:
            x = 10 + (self.width() - 20) * pos
            painter.setBrush(QBrush(QColor(0, 150, 255))); painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(x, track_y), 5, 5)
        handle_x = 10 + (self.width() - 20) * self.slider_position
        painter.setBrush(QBrush(QColor(255, 100, 100))); painter.setPen(QPen(QColor(200, 50, 50), 2))
        painter.drawEllipse(QPointF(handle_x, track_y), 8, 8)
    def mousePressEvent(self, event): self._update_slider_position(event.position().x())
    def mouseMoveEvent(self, event): self._update_slider_position(event.position().x())
    def _update_slider_position(self, x_pos):
        pos = (x_pos - 10) / (self.width() - 20)
        self.slider_position = max(0.0, min(1.0, pos))
        self.sliderMoved.emit(self.slider_position)
        self.update()

# --- Audio Engine ---
class AudioEngine(QObject):
    def __init__(self):
        super().__init__()
        self.instrumental = None
        self.vocals = []
        self.vocal_id_counter = 0
        self.slider_pos = 0.0
        self.instrumental_volume = 1.0
        self.vocal_master_volume = 1.0
        self.is_playing = False
        self.playback_thread = None
        self.lock = threading.Lock()
        self.vocal_arrays = {}
        self.last_playback_frame = 0

    def set_instrumental(self, file_path):
        try:
            seg = AudioSegment.from_file(file_path).set_frame_rate(TARGET_SAMPLE_RATE).set_channels(TARGET_CHANNELS).set_sample_width(TARGET_SAMPLE_WIDTH)
            self.instrumental = seg
            return file_path.split('/')[-1]
        except Exception as e: return None
            
    def _redistribute_vocals(self):
        num_vocals = len(self.vocals)
        self.vocals.sort(key=lambda v: v['id'])
        for i, vocal in enumerate(self.vocals):
            vocal['pos'] = (i + 1) / (num_vocals + 1.0)
        if num_vocals > 0: print(f"Redistributed {num_vocals} vocals.")

    def add_vocal(self, file_path):
        try:
            seg = AudioSegment.from_file(file_path).set_frame_rate(TARGET_SAMPLE_RATE).set_channels(TARGET_CHANNELS).set_sample_width(TARGET_SAMPLE_WIDTH)
            vocal_data = {'seg': seg, 'processed_seg': seg, 'pos': 0.0, 'name': file_path.split('/')[-1], 'id': self.vocal_id_counter, 'volume': 1.0, 'pitch': 0}
            self.vocal_id_counter += 1
            self.vocals.append(vocal_data)
            self._redistribute_vocals()
            return vocal_data
        except Exception as e: return None

    def remove_vocal(self, index_to_remove):
        if 0 <= index_to_remove < len(self.vocals):
            removed_vocal = self.vocals.pop(index_to_remove)
            self.vocal_arrays.pop(removed_vocal['id'], None)
            self._redistribute_vocals()
            return True
        return False

    def update_vocal_settings(self, index, volume=None, pitch=None):
        if not (0 <= index < len(self.vocals)): return
        vocal = self.vocals[index]
        if volume is not None:
            vocal['volume'] = volume
        if pitch is not None and vocal['pitch'] != pitch:
            vocal['pitch'] = pitch
            print(f"Processing pitch shift ({pitch} semitones) for {vocal['name']}...")
            
            # If pitch is zero, just use the original segment
            if pitch == 0:
                vocal['processed_seg'] = vocal['seg']
            else:
                # 1. Get audio data as a NumPy array
                samples = np.array(vocal['seg'].get_array_of_samples()).reshape((-1, vocal['seg'].channels)).T
                
                # 2. Convert to float for librosa
                samples_float = samples.astype(np.float32) / (2**(8 * vocal['seg'].sample_width - 1))

                # 3. Apply pitch shift with librosa
                shifted_samples = librosa.effects.pitch_shift(y=samples_float, sr=vocal['seg'].frame_rate, n_steps=pitch)

                # 4. Convert back to integer format for pydub
                shifted_samples_int = (shifted_samples * (2**(8 * vocal['seg'].sample_width - 1))).astype(samples.dtype)

                # 5. Create new AudioSegment from processed data
                vocal['processed_seg'] = AudioSegment(
                    data=shifted_samples_int.T.tobytes(),
                    sample_width=vocal['seg'].sample_width,
                    frame_rate=vocal['seg'].frame_rate,
                    channels=vocal['seg'].channels
                )
            
            self.vocal_arrays[vocal['id']] = np.array(vocal['processed_seg'].get_array_of_samples())
            print("Pitch shift processing complete.")

    def update_vocal_position(self, index, new_pos):
        if 0 <= index < len(self.vocals): self.vocals[index]['pos'] = new_pos
    def update_slider_position(self, pos):
        with self.lock: self.slider_pos = pos
    def set_instrumental_volume(self, volume):
        with self.lock: self.instrumental_volume = volume
    def set_vocal_master_volume(self, volume):
        with self.lock: self.vocal_master_volume = volume

    def play(self, start_frame=0):
        if self.is_playing or not self.instrumental: return
        self.is_playing = True
        self.playback_thread = threading.Thread(target=self._playback_loop, args=(start_frame,))
        self.playback_thread.daemon = True
        self.playback_thread.start()

    def stop(self):
        self.is_playing = False
        if self.playback_thread: self.playback_thread.join()

    def _calculate_crossfade_gains(self, slider_pos, vocals):
        num_vocals = len(vocals)
        gains = {v['id']: 0.0 for v in vocals}
        if num_vocals == 0: return gains
        if num_vocals == 1:
            gains[vocals[0]['id']] = 1.0
            return gains
        sorted_vocals = sorted(vocals, key=lambda v: v['pos'])
        if slider_pos <= sorted_vocals[0]['pos']:
            gains[sorted_vocals[0]['id']] = 1.0
            return gains
        if slider_pos >= sorted_vocals[-1]['pos']:
            gains[sorted_vocals[-1]['id']] = 1.0
            return gains
        right_neighbor = next((v for v in sorted_vocals if v['pos'] >= slider_pos), None)
        right_neighbor_index = sorted_vocals.index(right_neighbor)
        left_neighbor = sorted_vocals[right_neighbor_index - 1]
        pos_a, pos_b = left_neighbor['pos'], right_neighbor['pos']
        if pos_b == pos_a:
            gains[left_neighbor['id']] = 1.0
            return gains
        progress = (slider_pos - pos_a) / (pos_b - pos_a)
        angle = progress * (math.pi / 2)
        gains[left_neighbor['id']] = math.cos(angle)
        gains[right_neighbor['id']] = math.sin(angle)
        return gains

    def _playback_loop(self, start_frame=0):
        p = pyaudio.PyAudio()
        stream = p.open(format=p.get_format_from_width(TARGET_SAMPLE_WIDTH), channels=TARGET_CHANNELS, rate=TARGET_SAMPLE_RATE, output=True)
        instrumental_array = np.array(self.instrumental.get_array_of_samples())
        self.vocal_arrays = {v['id']: np.array(v['processed_seg'].get_array_of_samples()) for v in self.vocals}
        current_frame, total_frames = start_frame, len(instrumental_array) // TARGET_CHANNELS

        while self.is_playing and current_frame < total_frames:
            with self.lock:
                current_slider_pos, vocals_copy = self.slider_pos, list(self.vocals)
                current_inst_vol, current_vocal_master_vol = self.instrumental_volume, self.vocal_master_volume
            start_idx, end_idx = current_frame * TARGET_CHANNELS, (current_frame + CHUNK_SIZE) * TARGET_CHANNELS
            inst_chunk_raw = instrumental_array[start_idx:end_idx]
            inst_chunk = (inst_chunk_raw.astype(np.float32) * current_inst_vol)
            if len(inst_chunk) < CHUNK_SIZE * TARGET_CHANNELS:
                inst_chunk = np.pad(inst_chunk, (0, CHUNK_SIZE * TARGET_CHANNELS - len(inst_chunk)), 'constant')
            mixed_vocals_chunk = np.zeros_like(inst_chunk, dtype=np.float32)
            gains = self._calculate_crossfade_gains(current_slider_pos, vocals_copy)
            for vocal in vocals_copy:
                gain, per_track_volume = gains.get(vocal['id'], 0.0), vocal.get('volume', 1.0)
                if gain > 0.001:
                    voc_chunk = self.vocal_arrays[vocal['id']][start_idx:end_idx]
                    if len(voc_chunk) < len(inst_chunk): voc_chunk = np.pad(voc_chunk, (0, len(inst_chunk) - len(voc_chunk)), 'constant')
                    mixed_vocals_chunk += voc_chunk.astype(np.float32) * gain * per_track_volume
            mixed_vocals_chunk *= current_vocal_master_vol
            final_mix = inst_chunk + mixed_vocals_chunk
            stream.write(np.clip(final_mix, -32768, 32767).astype(np.int16).tobytes())
            current_frame += CHUNK_SIZE
        self.last_playback_frame = current_frame
        stream.stop_stream(); stream.close(); p.terminate()
        self.is_playing = False

# --- Main Application Window ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TeX's Vocal Mezcla")
        self.setGeometry(100, 100, 800, 800)
        self.audio_engine = AudioEngine()
        main_widget = QWidget(); self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        self.instrumental_label = QLabel("No Instrumental Loaded")
        btn_load_instrumental = QPushButton("Load Instrumental")
        layout.addWidget(self.instrumental_label); layout.addWidget(btn_load_instrumental)
        vocal_layout = QHBoxLayout()
        self.vocal_list_widget = QListWidget()
        self.vocal_list_widget.setFixedWidth(300)
        vocal_tools_layout = QVBoxLayout()
        btn_add_vocal = QPushButton("Add Vocal(s)")
        self.btn_remove_vocal = QPushButton("Remove Selected Vocal")
        self.btn_remove_vocal.setEnabled(False)
        vocal_tools_layout.addWidget(btn_add_vocal); vocal_tools_layout.addWidget(self.btn_remove_vocal)
        vocal_layout.addWidget(self.vocal_list_widget); vocal_layout.addLayout(vocal_tools_layout)
        layout.addLayout(vocal_layout)

        self.selected_vocal_group = QGroupBox("Selected Vocal Settings")
        selected_vocal_layout = QVBoxLayout()
        pos_layout = QHBoxLayout(); pos_layout.addWidget(QLabel("Position:")); self.vocal_pos_slider = QSlider(Qt.Horizontal); pos_layout.addWidget(self.vocal_pos_slider); selected_vocal_layout.addLayout(pos_layout)
        vol_layout = QHBoxLayout(); self.selected_vol_label = QLabel("Volume: 100%"); self.selected_vol_slider = QSlider(Qt.Horizontal); self.selected_vol_slider.setRange(0, 150); self.selected_vol_slider.setValue(100); vol_layout.addWidget(self.selected_vol_label); vol_layout.addWidget(self.selected_vol_slider); selected_vocal_layout.addLayout(vol_layout)
        pitch_layout = QHBoxLayout(); self.selected_pitch_label = QLabel("Pitch: 0 st"); self.selected_pitch_slider = QSlider(Qt.Horizontal); self.selected_pitch_slider.setRange(-12, 12); self.selected_pitch_slider.setValue(0); pitch_layout.addWidget(self.selected_pitch_label); pitch_layout.addWidget(self.selected_pitch_slider); selected_vocal_layout.addLayout(pitch_layout)
        self.selected_vocal_group.setLayout(selected_vocal_layout); self.selected_vocal_group.setEnabled(False)
        layout.addWidget(self.selected_vocal_group)

        settings_group = QGroupBox("Global Settings")
        settings_layout = QVBoxLayout()
        inst_vol_layout = QHBoxLayout(); self.inst_vol_label = QLabel("Instrumental Vol: 100%"); self.inst_vol_slider = QSlider(Qt.Horizontal); self.inst_vol_slider.setRange(0, 150); self.inst_vol_slider.setValue(100); inst_vol_layout.addWidget(self.inst_vol_label); inst_vol_layout.addWidget(self.inst_vol_slider); settings_layout.addLayout(inst_vol_layout)
        vocal_vol_layout = QHBoxLayout(); self.vocal_vol_label = QLabel("Master Vocal Vol: 100%"); self.vocal_vol_slider = QSlider(Qt.Horizontal); self.vocal_vol_slider.setRange(0, 150); self.vocal_vol_slider.setValue(100); vocal_vol_layout.addWidget(self.vocal_vol_label); vocal_vol_layout.addWidget(self.vocal_vol_slider); settings_layout.addLayout(vocal_vol_layout)
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        layout.addWidget(QLabel("Blend Control")); self.blender_slider = AudioSlider(); layout.addWidget(self.blender_slider)
        controls_layout = QHBoxLayout(); self.btn_play = QPushButton("Play"); self.btn_stop = QPushButton("Stop"); controls_layout.addWidget(self.btn_play); controls_layout.addWidget(self.btn_stop); layout.addLayout(controls_layout)

        footer_label = QLabel("TeXmExDeX Type Tools"); footer_label.setAlignment(Qt.AlignRight); footer_font = footer_label.font(); footer_font.setPointSize(8); footer_label.setFont(footer_font); footer_label.setStyleSheet("color: #888;"); layout.addWidget(footer_label)
        
        btn_load_instrumental.clicked.connect(self.load_instrumental); btn_add_vocal.clicked.connect(self.add_vocals); self.btn_remove_vocal.clicked.connect(self.remove_selected_vocal)
        self.blender_slider.sliderMoved.connect(self.audio_engine.update_slider_position); self.btn_play.clicked.connect(self.play_from_start); self.btn_stop.clicked.connect(self.stop_playback)
        self.vocal_list_widget.currentRowChanged.connect(self.on_vocal_selected); self.vocal_pos_slider.valueChanged.connect(self.on_vocal_pos_changed)
        self.inst_vol_slider.valueChanged.connect(self.on_inst_vol_changed); self.vocal_vol_slider.valueChanged.connect(self.on_vocal_vol_changed)
        self.selected_vol_slider.valueChanged.connect(self.on_selected_vol_changed); self.selected_pitch_slider.sliderReleased.connect(self.on_selected_pitch_changed)

    def play_from_start(self): self.audio_engine.stop(); self.audio_engine.play(start_frame=0)
    def stop_playback(self): self.audio_engine.stop()
    def load_instrumental(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Instrumental", "", "Audio Files (*.wav *.mp3)")
        if file_path and (name := self.audio_engine.set_instrumental(file_path)): self.instrumental_label.setText(f"Instrumental: {name}")
    def add_vocals(self):
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Select Vocal Tracks", "", "Audio Files (*.wav *.mp3)")
        if not file_paths: return
        self.audio_engine.stop()
        for path in file_paths: self.audio_engine.add_vocal(path)
        self.vocal_list_widget.clear()
        for vocal in self.audio_engine.vocals: self.vocal_list_widget.addItem(QListWidgetItem(vocal['name']))
        self.update_blender_markers()
    def remove_selected_vocal(self):
        selected_index = self.vocal_list_widget.currentRow()
        if selected_index == -1: return
        self.audio_engine.stop(); self.audio_engine.remove_vocal(selected_index); self.vocal_list_widget.takeItem(selected_index); self.update_blender_markers()
    def on_vocal_selected(self, index):
        is_selected = index >= 0
        self.btn_remove_vocal.setEnabled(is_selected); self.selected_vocal_group.setEnabled(is_selected)
        if is_selected:
            vocal = self.audio_engine.vocals[index]
            self.vocal_pos_slider.setValue(int(vocal['pos'] * 100)); self.selected_vol_slider.setValue(int(vocal['volume'] * 100)); self.selected_pitch_slider.setValue(vocal['pitch'])
            self.selected_vol_label.setText(f"Volume: {int(vocal['volume'] * 100)}%"); self.selected_pitch_label.setText(f"Pitch: {vocal['pitch']} st")
    def on_vocal_pos_changed(self, value):
        index = self.vocal_list_widget.currentRow()
        if 0 <= index < len(self.audio_engine.vocals):
            self.audio_engine.update_vocal_position(index, value / 100.0); self.update_blender_markers()
    def on_selected_vol_changed(self, value):
        index = self.vocal_list_widget.currentRow()
        if 0 <= index < len(self.audio_engine.vocals):
            self.audio_engine.update_vocal_settings(index, volume=(value / 100.0)); self.selected_vol_label.setText(f"Volume: {value}%")
    def on_selected_pitch_changed(self):
        index = self.vocal_list_widget.currentRow()
        if index < 0: return
        was_playing = self.audio_engine.is_playing; self.audio_engine.stop()
        pitch = self.selected_pitch_slider.value()
        self.audio_engine.update_vocal_settings(index, pitch=pitch); self.selected_pitch_label.setText(f"Pitch: {pitch} st")
        if was_playing: self.audio_engine.play(start_frame=self.audio_engine.last_playback_frame)
    def update_blender_markers(self): self.blender_slider.set_vocal_positions([v['pos'] for v in self.audio_engine.vocals])
    def on_inst_vol_changed(self, value):
        self.audio_engine.set_instrumental_volume(value / 100.0); self.inst_vol_label.setText(f"Instrumental Vol: {value}%")
    def on_vocal_vol_changed(self, value):
        self.audio_engine.set_vocal_master_volume(value / 100.0); self.vocal_vol_label.setText(f"Master Vocal Vol: {value}%")
    def closeEvent(self, event): self.audio_engine.stop(); super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())