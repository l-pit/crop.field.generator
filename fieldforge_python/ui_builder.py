# SPDX-License-Identifier: GPL-3.0-or-later
# FieldForge - Isaac Sim extension for procedural agricultural fields.
# Copyright (c) 2026 Łukasiewicz – PIT
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import omni.ui as ui
from .config import FieldConfig, BedConfig, WeedConfig
from .field import Field
import omni.usd
import os
import asyncio
import random
import carb
from pxr import UsdGeom, Gf, Sdf

# NOTE: omni.replicator.core is imported lazily inside the capture methods.
# Importing it at module load happens during extension startup, before PhysX
# is initialized, which makes its native plugin fail to load. Deferring the
# import to capture time (after the app is fully up) avoids that.

class UIBuilder:
    def __init__(self):
        self._seed_slider = None
        self._width_field = None
        self._length_field = None

        self._writer = None
        self._render_product = None
        self._camera_prim = None
        self._output_dir = ""


        self._progress_bar = None
        self._progress_label = None
        self._capture_btn = None
        self._cancel_btn = None
        self._cancel_requested = False
        
        self._beds_frame = None
        self._weeds_frame = None
        self.field = Field()




    def build_ui(self):
        with ui.VStack(spacing=10, height=0):

            ui.Label("Seed")  
            self._seed_slider = ui.IntSlider(min=0, max=100, tooltip="Sets the initial random seed. Changing this generates a new random variation of the field.")
            self._seed_slider.model.set_value(0)
            with ui.HStack():
                ui.Label("Use Point Instancing", width=ui.Fraction(0.95), tooltip="Checked: Fast generation. Unchecked: Accurate bounding boxes for synthetic data.")
                self._instancing_checkbox = ui.CheckBox(width=ui.Fraction(0.05), tooltip="Checked: Fast generation. Unchecked: Accurate bounding boxes for synthetic data.")
                self._instancing_checkbox.model.set_value(self.field._cfg.use_point_instancer) 
                self._instancing_checkbox.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "use_point_instancer", m.as_bool), self.field._cfg.save_to_settings()))
            ui.Button("Generate", clicked_fn=self._on_click_update_seed)
            ui.Line()

            with ui.CollapsableFrame("Field Configuration", collapsed=False):
                with ui.VStack(spacing=6):
                    with ui.HStack(spacing=10):
                        ui.Label("Field Spawn Path", width=ui.Fraction(0.5), tooltip="Where the generated field will be created in the stage.")
                        f_path = ui.StringField()
                        f_path.model.set_value(self.field._cfg.field_prim_path)
                        f_path.model.add_end_edit_fn(lambda m: (
                            setattr(self.field._cfg, "field_prim_path", m.as_string),
                            self.field._cfg.save_to_settings()
                        ))
                    with ui.HStack(spacing=10):

                        ui.Label("Field Width", width=ui.Fraction(0.6))
                        width_drag = ui.FloatDrag(min=0.1)
                        width_drag.model.set_value(self.field._cfg.width)
                        width_drag.model.add_value_changed_fn(lambda m: self._set_width(m.as_float))

                        ui.Label("Field Length", width=ui.Fraction(0.6))
                        length_drag = ui.FloatDrag(min=0.1)
                        length_drag.model.set_value(self.field._cfg.length)
                        length_drag.model.add_value_changed_fn(lambda m: self._set_length(m.as_float))

                    with ui.HStack():
                        ui.Label("Edge Width", width=ui.Fraction(0.5))
                        edge_drag = ui.FloatDrag(min=0.0)
                        edge_drag.model.set_value(self.field._cfg.edge_width)
                        edge_drag.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "edge_width", m.as_float), self.field._cfg.save_to_settings()))

                    with ui.HStack():
                        ui.Label("Generate Uneven Ground", width=ui.Fraction(0.7), tooltip="If checked, replaces the flat ground plane with a physical, bumpy mesh.")
                        uneven_cb = ui.CheckBox(width=ui.Fraction(0.3))
                        uneven_cb.model.set_value(self.field._cfg.generate_uneven_ground)


                
            self._terrain_frame = ui.CollapsableFrame("Terrain Configuration", collapsed=True, visible=True)
            with self._terrain_frame:
                with ui.VStack(spacing=6):

                        with ui.VStack(spacing=6, height=0):
                            ui.Label("Generic uneven-ground noise is optional. Ridge / furrow can be enabled with or without it.")
                            
                            with ui.HStack(spacing=10):
                                ui.Label("Heightmap Image", width=120, tooltip="Absolute path to a grayscale heightmap image (e.g., .png). Leave this field EMPTY to auto-generate terrain using procedural noise.")
                                t_path = ui.StringField(tooltip="Absolute path to a grayscale heightmap image (e.g., .png). Leave this field EMPTY to auto-generate terrain using procedural noise.")
                                t_path.model.set_value(self.field._cfg.terrain_image_path)
                                t_path.model.add_end_edit_fn(lambda m: (
                                    setattr(self.field._cfg, "terrain_image_path", m.as_string),
                                    self.field._cfg.save_to_settings()
                                ))
                            
                            with ui.HStack(spacing=10):
                                ui.Label("Max Height (m)", width=ui.Fraction(0.5), tooltip="The maximum physical height of the terrain in meters. Pure white pixels (or maximum noise peaks) will reach this height.")
                                t_max = ui.FloatDrag(min=0.01, max=5.0, step=0.05, tooltip="The maximum physical height of the terrain in meters. Pure white pixels (or maximum noise peaks) will reach this height.")
                                t_max.model.set_value(self.field._cfg.terrain_max_height)
                                t_max.model.add_value_changed_fn(lambda m: (
                                    setattr(self.field._cfg, "terrain_max_height", m.as_float),
                                    self.field._cfg.save_to_settings()
                                ))

                            with ui.HStack(spacing=10):
                                ui.Label("Resolution", width=ui.Fraction(0.5), tooltip="Vertex density of the terrain mesh. Higher values create smoother, more detailed physics meshes but consume more memory and slow down simulation.")
                                res_drag = ui.FloatDrag(min=1.0, step=1.0, tooltip="Vertex density of the terrain mesh. Higher values create smoother, more detailed physics meshes but consume more memory and slow down simulation.")
                                res_drag.model.set_value(self.field._cfg.noise_resolution)
                                res_drag.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "noise_resolution", m.as_float), self.field._cfg.save_to_settings()))

                            ui.Separator()
                            ui.Label("Parameters for  procedural generation: ", tooltip="These settings only apply if the Heightmap Image path above is left empty.")
                            
                            
                            with ui.HStack(spacing=10):
                                ui.Label("Scale", width=ui.Fraction(0.5), tooltip="Base zoom level for the noise. Higher values stretch the noise into broad, rolling hills. Lower values create tight, frequent bumps.")
                                scale_drag = ui.FloatDrag(min=0.1, step=0.1, tooltip="Base zoom level for the noise. Higher values stretch the noise into broad, rolling hills. Lower values create tight, frequent bumps.")
                                scale_drag.model.set_value(self.field._cfg.terrain_scale)
                                scale_drag.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "terrain_scale", m.as_float), self.field._cfg.save_to_settings()))
                            
                            with ui.HStack(spacing=10):
                                ui.Label("Octaves", width=ui.Fraction(0.5), tooltip="Number of noise layers added together. 1 is very smooth. 4+ adds increasingly smaller, jagged details (like rocks and dirt clumps).")
                                oct_drag = ui.IntDrag(min=1, max=10, tooltip="Number of noise layers added together. 1 is very smooth. 4+ adds increasingly smaller, jagged details (like rocks and dirt clumps).")
                                oct_drag.model.set_value(self.field._cfg.terrain_octaves)
                                oct_drag.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "terrain_octaves", m.as_int), self.field._cfg.save_to_settings()))

                            with ui.HStack(spacing=10):
                                ui.Label("Persistence", width=ui.Fraction(0.5), tooltip="How much each successive octave layer affects the overall height. Higher values make the small details more pronounced and the terrain rougher.")
                                oct_drag = ui.FloatDrag(min=0, max=5.0, tooltip="How much each successive octave layer affects the overall height. Higher values make the small details more pronounced and the terrain rougher.")
                                oct_drag.model.set_value(self.field._cfg.terrain_persistence)
                                oct_drag.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "terrain_persistence", m.as_float), self.field._cfg.save_to_settings()))

                            with ui.HStack(spacing=10):
                                ui.Label("Lacunarity", width=ui.Fraction(0.5), tooltip="How quickly the detail frequency increases with each octave layer. Higher values pack finer, tighter details into smaller areas.")
                                oct_drag = ui.FloatDrag(min=0, max=5.0, tooltip="How quickly the detail frequency increases with each octave layer. Higher values pack finer, tighter details into smaller areas.")
                                oct_drag.model.set_value(self.field._cfg.terrain_lacunarity)
                                oct_drag.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "terrain_lacunarity", m.as_float), self.field._cfg.save_to_settings()))

                            ui.Separator()
                            with ui.HStack():
                                ui.Label("Simulate Ridge / Furrow", width=ui.Fraction(0.7), tooltip="Adds a crop-row-aligned ridge/furrow profile derived from the current bed row spacing and row-shape formula. Works even if generic uneven noise is disabled.")
                                ridge_cb = ui.CheckBox(width=ui.Fraction(0.3))
                                ridge_cb.model.set_value(self.field._cfg.simulate_ridge_furrow)

                            ridge_frame = ui.CollapsableFrame("Ridge / Furrow Settings", collapsed=False, visible=self.field._cfg.simulate_ridge_furrow)
                            with ridge_frame:
                                with ui.VStack(spacing=6, height=0):
                                    ui.Label("Defaults are tuned for a mild early-corn field. Ridge spacing follows each bed's row spacing automatically.")
                                    with ui.HStack(spacing=10):
                                        ui.Label("Ridge Height (m)", width=ui.Fraction(0.5), tooltip="Height of the row crest above the local terrain baseline. Default 0.04 m for a mild early-corn field.")
                                        ridge_height = ui.FloatDrag(min=0.0, max=0.3, step=0.005)
                                        ridge_height.model.set_value(self.field._cfg.ridge_height)
                                        ridge_height.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "ridge_height", m.as_float), self.field._cfg.save_to_settings()))

                                    with ui.HStack(spacing=10):
                                        ui.Label("Furrow Depth (m)", width=ui.Fraction(0.5), tooltip="Depth of the inter-row trough below the local terrain baseline. Default 0.03 m for a mild early-corn field.")
                                        furrow_depth = ui.FloatDrag(min=0.0, max=0.3, step=0.005)
                                        furrow_depth.model.set_value(self.field._cfg.furrow_depth)
                                        furrow_depth.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "furrow_depth", m.as_float), self.field._cfg.save_to_settings()))

                                    with ui.HStack(spacing=10):
                                        ui.Label("Ridge Steepness", width=ui.Fraction(0.5), tooltip="Sharpness of the ridge cross-section. 1.0 = soft cosine (default). >1 makes the crest more pointy; <1 makes it more rounded/plateau-like.")
                                        ridge_steep = ui.FloatDrag(min=0.2, max=8.0, step=0.1)
                                        ridge_steep.model.set_value(self.field._cfg.ridge_steepness)
                                        ridge_steep.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "ridge_steepness", m.as_float), self.field._cfg.save_to_settings()))

                                    with ui.HStack(spacing=10):
                                        ui.Label("Micro-Detail Strength", width=ui.Fraction(0.5), tooltip="Strength of the small along-ridge bumps: planter/seed-opener marks at the plant_distance period and a slow ~15% crest variation. 0 disables them and gives perfectly uniform ridges. Default 1.0.")
                                        ridge_micro = ui.FloatDrag(min=0.0, max=3.0, step=0.05)
                                        ridge_micro.model.set_value(self.field._cfg.ridge_micro_strength)
                                        ridge_micro.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "ridge_micro_strength", m.as_float), self.field._cfg.save_to_settings()))

                            ui.Separator()

                            # ---------------- Anisotropic noise ----------------
                            with ui.HStack():
                                ui.Label("Anisotropic Surface Noise", width=ui.Fraction(0.7), tooltip="Adds directional surface roughness on top of any other noise: smoother along the rows, rougher across them. Combine with the isotropic procedural noise above by enabling both checkboxes — they are summed.")
                                aniso_cb = ui.CheckBox(width=ui.Fraction(0.3))
                                aniso_cb.model.set_value(self.field._cfg.aniso_noise_enabled)

                            aniso_frame = ui.CollapsableFrame("Anisotropic Noise Settings", collapsed=False, visible=self.field._cfg.aniso_noise_enabled)
                            with aniso_frame:
                                with ui.VStack(spacing=6, height=0):
                                    ui.Label("Defaults match WSU's typical 5-20 mm random-roughness range for tilled fields.")
                                    with ui.HStack(spacing=10):
                                        ui.Label("Amplitude (m, RMS)", width=ui.Fraction(0.5), tooltip="Root-mean-square height of the directional noise in metres. 0.012 m = 12 mm RMS, near the high end of typical tilled-field roughness.")
                                        a_amp = ui.FloatDrag(min=0.0, max=0.10, step=0.001)
                                        a_amp.model.set_value(self.field._cfg.aniso_amplitude)
                                        a_amp.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "aniso_amplitude", m.as_float), self.field._cfg.save_to_settings()))

                                    with ui.HStack(spacing=10):
                                        ui.Label("Smooth Along Rows (m)", width=ui.Fraction(0.5), tooltip="Gaussian sigma along Y (rows direction). Larger = smoother along the row. Default 0.40 m.")
                                        a_along = ui.FloatDrag(min=0.01, max=2.0, step=0.05)
                                        a_along.model.set_value(self.field._cfg.aniso_smooth_along_m)
                                        a_along.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "aniso_smooth_along_m", m.as_float), self.field._cfg.save_to_settings()))

                                    with ui.HStack(spacing=10):
                                        ui.Label("Smooth Across Rows (m)", width=ui.Fraction(0.5), tooltip="Gaussian sigma along X (cross-row). Smaller = sharper across-row clumps. Default 0.10 m.")
                                        a_across = ui.FloatDrag(min=0.01, max=2.0, step=0.01)
                                        a_across.model.set_value(self.field._cfg.aniso_smooth_across_m)
                                        a_across.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "aniso_smooth_across_m", m.as_float), self.field._cfg.save_to_settings()))

                            ui.Separator()

                            # ---------------- Clods ----------------
                            with ui.HStack():
                                ui.Label("Clods (aggregate bumps)", width=ui.Fraction(0.7), tooltip="Discrete soil aggregates: small Gaussian bumps scattered uniformly across the field. Adds high-frequency detail that smoothed noise cannot produce.")
                                clods_cb = ui.CheckBox(width=ui.Fraction(0.3))
                                clods_cb.model.set_value(self.field._cfg.clods_enabled)

                            clods_frame = ui.CollapsableFrame("Clod Settings", collapsed=False, visible=self.field._cfg.clods_enabled)
                            with clods_frame:
                                with ui.VStack(spacing=6, height=0):
                                    ui.Label("Each clod is a small Gaussian bump. Cost grows with density × area.")
                                    with ui.HStack(spacing=10):
                                        ui.Label("Density (per m²)", width=ui.Fraction(0.5), tooltip="Average number of clods per square metre. Default 5/m². Reduce on large fields if FPS drops.")
                                        c_den = ui.FloatDrag(min=0.0, max=200.0, step=0.5)
                                        c_den.model.set_value(self.field._cfg.clod_density)
                                        c_den.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "clod_density", m.as_float), self.field._cfg.save_to_settings()))

                                    with ui.HStack(spacing=10):
                                        ui.Label("Min / Max Radius (m)", width=ui.Fraction(0.5), tooltip="Range of clod horizontal radii in metres. Defaults 0.02 / 0.05 m.")
                                        c_rmin = ui.FloatDrag(min=0.005, max=0.5, step=0.005)
                                        c_rmin.model.set_value(self.field._cfg.clod_min_radius)
                                        c_rmin.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "clod_min_radius", m.as_float), self.field._cfg.save_to_settings()))
                                        c_rmax = ui.FloatDrag(min=0.005, max=0.5, step=0.005)
                                        c_rmax.model.set_value(self.field._cfg.clod_max_radius)
                                        c_rmax.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "clod_max_radius", m.as_float), self.field._cfg.save_to_settings()))

                                    with ui.HStack(spacing=10):
                                        ui.Label("Min / Max Height (m)", width=ui.Fraction(0.5), tooltip="Range of clod heights in metres. Defaults 0.005 / 0.015 m.")
                                        c_hmin = ui.FloatDrag(min=0.0, max=0.20, step=0.001)
                                        c_hmin.model.set_value(self.field._cfg.clod_min_height)
                                        c_hmin.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "clod_min_height", m.as_float), self.field._cfg.save_to_settings()))
                                        c_hmax = ui.FloatDrag(min=0.0, max=0.20, step=0.001)
                                        c_hmax.model.set_value(self.field._cfg.clod_max_height)
                                        c_hmax.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "clod_max_height", m.as_float), self.field._cfg.save_to_settings()))

                def on_uneven_toggle(m):
                    is_active = m.as_bool
                    self.field._cfg.generate_uneven_ground = is_active
                    self.field._cfg.save_to_settings()

                def on_ridge_toggle(m):
                    is_active = m.as_bool
                    self.field._cfg.simulate_ridge_furrow = is_active
                    self.field._cfg.save_to_settings()
                    ridge_frame.visible = is_active

                def on_aniso_toggle(m):
                    is_active = m.as_bool
                    self.field._cfg.aniso_noise_enabled = is_active
                    self.field._cfg.save_to_settings()
                    aniso_frame.visible = is_active

                def on_clods_toggle(m):
                    is_active = m.as_bool
                    self.field._cfg.clods_enabled = is_active
                    self.field._cfg.save_to_settings()
                    clods_frame.visible = is_active

                uneven_cb.model.add_value_changed_fn(on_uneven_toggle)
                ridge_cb.model.add_value_changed_fn(on_ridge_toggle)
                aniso_cb.model.add_value_changed_fn(on_aniso_toggle)
                clods_cb.model.add_value_changed_fn(on_clods_toggle)

            with ui.CollapsableFrame("Stage Prim References", collapsed=False):
                with ui.VStack(spacing=6, padding=5, height=0):
                    with ui.HStack(spacing=10):
                        ui.Label("Materials Parent Prim", width=ui.Fraction(0.5), tooltip="Stage path to the prim containing material objects.")
                        m_path = ui.StringField()
                        m_path.model.set_value(self.field._cfg.materials_parent_path)
                        m_path.model.add_end_edit_fn(lambda m: (
                            setattr(self.field._cfg, "materials_parent_path", m.as_string),
                            self.field._cfg.save_to_settings()
                        ))

                    with ui.HStack(spacing=10):
                        ui.Label("Lights Parent Prim", width=ui.Fraction(0.5), tooltip="Stage path to the prim containing light objects.")
                        l_path = ui.StringField()
                        l_path.model.set_value(self.field._cfg.lights_parent_path)
                        l_path.model.add_end_edit_fn(lambda m: (
                            setattr(self.field._cfg, "lights_parent_path", m.as_string),
                            self.field._cfg.save_to_settings(),
                            self._validate_capture_state()  
                        ))


                    with ui.CollapsableFrame("Lighting Variation"):
                        with ui.VStack(spacing=8, padding=5):
                            ui.Label("Sun Tilt (X-Axis: 0 to 360)")
                            with ui.HStack(spacing=10):
                                x_min = ui.FloatDrag(min=0, max=360, step=1)
                                x_min.model.set_value(self.field._cfg.light_x_range[0])
                                x_max = ui.FloatDrag(min=0, max=360, step=1)
                                x_max.model.set_value(self.field._cfg.light_x_range[1])
                                
                                x_min.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "light_x_range", (m.as_float, x_max.model.as_float)), self.field._cfg.save_to_settings()))
                                x_max.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "light_x_range", (x_min.model.as_float, m.as_float)), self.field._cfg.save_to_settings()))

                            ui.Label("Sun Height (Y-Axis: -90 to 90)")
                            with ui.HStack(spacing=10):
                                y_min = ui.FloatDrag(min=-90, max=90, step=1)
                                y_min.model.set_value(self.field._cfg.light_y_range[0])
                                y_max = ui.FloatDrag(min=-90, max=90, step=1)
                                y_max.model.set_value(self.field._cfg.light_y_range[1])
                                
                                y_min.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "light_y_range", (m.as_float, y_max.model.as_float)), self.field._cfg.save_to_settings()))
                                y_max.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "light_y_range", (y_min.model.as_float, m.as_float)), self.field._cfg.save_to_settings()))
                            
                            ui.Label("Sun Direction (Z-Axis Azimuth: 0 to 360)")
                            with ui.HStack(spacing=10):
                                z_min = ui.FloatDrag(min=0, max=360, step=1)
                                z_min.model.set_value(self.field._cfg.light_z_range[0])
                                z_max = ui.FloatDrag(min=0, max=360, step=1)
                                z_max.model.set_value(self.field._cfg.light_z_range[1])
                                
                                z_min.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "light_z_range", (m.as_float, z_max.model.as_float)), self.field._cfg.save_to_settings()))
                                z_max.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "light_z_range", (z_min.model.as_float, m.as_float)), self.field._cfg.save_to_settings()))


            with ui.CollapsableFrame("Field Beds", collapsed=False):
                with ui.VStack(spacing=8):
                    self._beds_frame = ui.Frame(build_fn=self._build_beds_panel)

            with ui.CollapsableFrame("Weeds Configuration", collapsed=True):
                with ui.VStack(spacing=8):
                    self._weeds_frame = ui.Frame(build_fn=self._build_weeds_panel)

            with ui.VStack(spacing=10):
                ui.Separator()
                ui.Label("Replicator Capture Settings", style={"font_size": 23})

                self._warning_label = ui.Label("", style={"color": "orange", "font_size": 14})
                self._warning_label.visible = False
                                        

                with ui.HStack(spacing=6):
                    ui.Label("Camera Prim Path", width=ui.Fraction(0.4), tooltip="The Stage path for the render camera. You can point this to an existing camera you have manually configured, or let the generator create a default top-down view for you.")
                    c_path = ui.StringField()
                    c_path.model.set_value(self.field._cfg.camera_prim_path)
                    c_path.model.add_end_edit_fn(lambda m: (
                        setattr(self.field._cfg, "camera_prim_path", m.as_string),
                        self.field._cfg.save_to_settings()
                        ))
                
                with ui.CollapsableFrame("Camera Randomization", collapsed=True):
                    with ui.VStack(spacing=8, padding=5, height=0):
            
                        ui.Label("Camera Height (Meters above ground)")
                        with ui.HStack(spacing=10, height=22):
                            h_min = ui.FloatDrag(min=0.1, max=10.0, step=0.1, tooltip="Minimum distance from the ground.")
                            h_min.model.set_value(self.field._cfg.camera_height_range[0])
                            h_max = ui.FloatDrag(min=0.1, max=10.0, step=0.1, tooltip="Maximum distance from the ground.")
                            h_max.model.set_value(self.field._cfg.camera_height_range[1])
                            
                            
                            h_min.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "camera_height_range", (m.as_float, h_max.model.as_float)), self.field._cfg.save_to_settings()))
                            h_max.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "camera_height_range", (h_min.model.as_float, m.as_float)), self.field._cfg.save_to_settings()))

                        ui.Label("Horizontal Drift (X/Y Offset in meters)")
                        with ui.HStack(spacing=10, height=22):
                            o_min = ui.FloatDrag(min=-2.0, max=2.0, step=0.01, tooltip="Minimum horizontal shift from center.")
                            o_min.model.set_value(self.field._cfg.camera_offset_range[0])
                            o_max = ui.FloatDrag(min=-2.0, max=2.0, step=0.01, tooltip="Maximum horizontal shift from center.")
                            o_max.model.set_value(self.field._cfg.camera_offset_range[1])
                            
                            o_min.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "camera_offset_range", (m.as_float, o_max.model.as_float)), self.field._cfg.save_to_settings()))
                            o_max.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "camera_offset_range", (o_min.model.as_float, m.as_float)), self.field._cfg.save_to_settings()))

                    
                        ui.Label("Camera Tilt Jitter (Degrees)")
                        with ui.HStack(spacing=10, height=22):
                            t_min = ui.FloatDrag(min=-45.0, max=45.0, step=0.5, tooltip="Minimum tilt variation.")
                            t_min.model.set_value(self.field._cfg.camera_tilt_range[0])
                            t_max = ui.FloatDrag(min=-45.0, max=45.0, step=0.5, tooltip="Maximum tilt variation.")
                            t_max.model.set_value(self.field._cfg.camera_tilt_range[1])
                            

                            t_min.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "camera_tilt_range", (m.as_float, t_max.model.as_float)), self.field._cfg.save_to_settings()))
                            t_max.model.add_value_changed_fn(lambda m: (setattr(self.field._cfg, "camera_tilt_range", (t_min.model.as_float, m.as_float)), self.field._cfg.save_to_settings()))
                

                with ui.HStack(spacing=6):
                    

                    ui.Label("N of Frames:", width=80, tooltip="Total number of frames to generate.")
                    self._frames_field = ui.IntField(width=ui.Fraction(0.2))
                    self._frames_field.model.set_value(self.field._cfg.capture_frames)
                    self._frames_field.model.add_value_changed_fn(
                        lambda m: (setattr(self.field._cfg, "capture_frames", m.as_int), self.field._cfg.save_to_settings())
                    )


                    ui.Label("Output Dir:", width=80, tooltip="The absolute local path where images and metadata will be saved.")
                    self._out_field = ui.StringField(tooltip="Example: /home/user/datasets/corn_run_01\nReplicator will create subfolders for each render type.")
                    self._out_field.model.set_value(self.field._cfg.output_directory)
                    self._out_field.model.add_end_edit_fn(
                        lambda m: (setattr(self.field._cfg, "output_directory", m.as_string), self.field._cfg.save_to_settings(), self._validate_capture_state())
                        )
                    
            

                with ui.HStack(spacing=6):
                    self._capture_btn = ui.Button("Capture N frames (seed++ each frame)", clicked_fn=self._on_click_capture)
                    self._cancel_btn = ui.Button("Cancel", clicked_fn=self._on_click_cancel, enabled=False)

                with ui.HStack(spacing=6):
                    self._progress_bar = ui.ProgressBar(width=0.6)
                    self._progress_bar.model.set_value(0.0)      
                    self._progress_bar.visible = False           
                    self._progress_label = ui.Label("", style={"color": 0x888888ff}, width=0.4)
                

        try:
            self._update_ground()
        except Exception as e:
            carb.log_warn(f"Failed to update ground preview: {e}")

        self._validate_capture_state()

    # --- Callbacks ---
    def _on_click_update_seed(self):
        seed = self._seed_slider.model.as_int
        self.field._on_seed_update(seed)

    def _on_click_cancel(self):
        self._cancel_requested = True

    def _build_beds_panel(self):
        """Dynamic UI for Multiple Crop Beds"""
        with ui.VStack(spacing=8, height=0):
            if not self.field._cfg.beds:
                ui.Label("No beds defined.", style={"color": 0x888888ff})
                ui.Button("Add New Bed", clicked_fn=self._on_click_add_bed, height=0.1)
                return

            for idx, bed in enumerate(self.field._cfg.beds):
                ui.Separator()
                with ui.CollapsableFrame(f"Bed {idx+1}: {bed.name}", collapsed=False):
                    with ui.VStack(spacing=4):

                        with ui.HStack(spacing=6):
                            ui.Label("Bed Name", width=80, tooltip="A label for this bed prim.")
                            name_f = ui.StringField(tooltip="A label for this bed prim.")
                            name_f.model.set_value(bed.name)
                            name_f.model.add_end_edit_fn(lambda m, b=bed: (setattr(b, "name", m.as_string), self._beds_frame.rebuild(), self.field._cfg.save_to_settings()))
                            
                            ui.Button("Remove", width=60, clicked_fn=lambda i=idx: self._remove_bed(i))

                        with ui.HStack(spacing=6):
                            ui.Label("Path Template", width=80, tooltip="The absolute path to models. Use {num} or {num:05d} as a placeholder for randomized asset selection.")
                            path_f = ui.StringField(tooltip="The absolute path to models. Use {num} or {num:05d} as a placeholder for randomized asset selection. Example: /root/Corn/V2/Corn_{num:05d}.usd")
                            path_f.model.set_value(bed.path_template)
                            path_f.model.add_end_edit_fn(lambda m, b=bed: (
                                    setattr(b, "path_template", m.as_string),
                                    self.field._cfg.save_to_settings(),
                                    self._validate_capture_state()
                                ))
                        
                        with ui.HStack(spacing=10):
                            ui.Label("Semantic Label", width=ui.Fraction(0.2), tooltip="Label for Replicator segmentation.")
                            label_f = ui.StringField(width=ui.Fraction(0.3))
                            label_f.model.set_value(bed.semantic_label)
                            label_f.model.add_end_edit_fn(lambda m, b=bed: (setattr(b, "semantic_label", m.as_string), self.field._cfg.save_to_settings()))
                            

                        with ui.HStack(spacing=10):
                            ui.Label("Model Count", width=ui.Fraction(0.2), tooltip="Maximum number of model variations available in the folder.")
                            count_f = ui.IntField(width=ui.Fraction(0.3))
                            count_f.model.set_value(bed.model_count)
                            count_f.model.add_value_changed_fn(lambda m, b=bed: (setattr(b, "model_count", m.as_int), self.field._cfg.save_to_settings()))

                            ui.Label("Force unique", width=ui.Fraction(0.45))
                            force_unique = ui.CheckBox(width=ui.Fraction(0.05))
                            force_unique.model.set_value(bed.force_unique) 
                            force_unique.model.add_value_changed_fn(lambda m: (setattr(bed, "force_unique", m.as_bool), self.field._cfg.save_to_settings()))

                        with ui.HStack(spacing=10):
                            ui.Label("Rows", width=ui.Fraction(0.2), tooltip="The number of parallel rows of plants to be generated within this specific bed.")
                            r_drag = ui.IntDrag(min=1, width=ui.Fraction(0.3), tooltip="The number of parallel rows of plants to be generated within this specific bed.")
                            r_drag.model.set_value(bed.rows)
                            r_drag.model.add_value_changed_fn(lambda m, b=bed: (setattr(b, "rows", m.as_int), self.field._cfg.save_to_settings()))
                            
                            ui.Label("Bed Width", width=ui.Fraction(0.2), tooltip="The total lateral space allocated to this bed before the next bed starts.")
                            bw_drag = ui.FloatDrag(min=0.1, width=ui.Fraction(0.3), tooltip="The total lateral space allocated to this bed before the next bed starts.")
                            bw_drag.model.set_value(bed.bed_width, )
                            bw_drag.model.add_value_changed_fn(lambda m, b=bed: (setattr(b, "bed_width", m.as_float), self.field._cfg.save_to_settings()))

                        with ui.HStack(spacing=10):
                            ui.Label("Math formula", width=ui.Fraction(0.2), tooltip="Enter a math formula to curve the row. 'x' represents the distance along the row. Examples: 'sin(x * 0.5) * 2' for a wave, or '0.05 * x**2' for an arc. Leave blank for straight rows. Supported: sin, cos, tan, abs, sqrt, pi, rand, noise.")
                            r_form = ui.StringField(width=ui.Fraction(0.8), tooltip="Enter a math formula to curve the row. 'x' represents the distance along the row. Examples: 'sin(x * 0.5) * 2' for a wave, or '0.05 * x**2' for an arc. Leave blank for straight rows. Supported: sin, cos, tan, abs, sqrt, pi, rand, noise.")
                            r_form.model.set_value(bed.math_formula)
                            r_form.model.add_end_edit_fn(lambda m, b=bed: (setattr(b, "math_formula", m.as_string), self.field._cfg.save_to_settings()))

                        with ui.HStack(spacing=10):
                            ui.Label("Row Spacing", width=ui.Fraction(0.2), tooltip="The distance (in meters) between the centers of each row within this bed.")
                            rs_drag = ui.FloatDrag(min=0.01, width=ui.Fraction(0.3), tooltip="The distance (in meters) between the centers of each row within this bed.")
                            rs_drag.model.set_value(bed.row_spacing)
                            rs_drag.model.add_value_changed_fn(lambda m, b=bed: (setattr(b, "row_spacing", m.as_float), self.field._cfg.save_to_settings()))

                            ui.Label("Plant Dist", width=ui.Fraction(0.2), tooltip="The longitudinal distance (in meters) between individual plants within a single row.")
                            pd_drag = ui.FloatDrag(min=0.01, width=ui.Fraction(0.3), tooltip="The longitudinal distance (in meters) between individual plants within a single row.")
                            pd_drag.model.set_value(bed.plant_distance)
                            pd_drag.model.add_value_changed_fn(lambda m, b=bed: (setattr(b, "plant_distance", m.as_float), self.field._cfg.save_to_settings()))
                        with ui.HStack(spacing=10):
                            ui.Label("Plant Jitter", width=ui.Fraction(0.2), tooltip="Random offset applied to each plant's X and Y position to simulate natural growth irregularity.")
                            jitter_drag = ui.FloatDrag(min=0.0, step=0.01, width=ui.Fraction(0.3))
                            jitter_drag.model.set_value(bed.plant_jitter)
                            jitter_drag.model.add_value_changed_fn(lambda m, b=bed: (setattr(b, "plant_jitter", m.as_float), self.field._cfg.save_to_settings()))
                            
                            ui.Label("Skip Chance", width=ui.Fraction(0.2), tooltip="Probability (0.0 to 1.0) that a plant will be missing from its spot in the row.")
                            skip_drag = ui.FloatDrag(min=0.0, max=1.0, step=0.05, width=ui.Fraction(0.3))
                            skip_drag.model.set_value(bed.skip_chance)
                            skip_drag.model.add_value_changed_fn(lambda m, b=bed: (setattr(b, "skip_chance", m.as_float), self.field._cfg.save_to_settings()))
                        
            ui.Button("Add New Bed", clicked_fn=self._on_click_add_bed, height=0.1)

    def _build_weeds_panel(self):
        """Dynamic UI for Multiple Weed Species with Grid Alignment and Tooltips"""
        with ui.VStack(spacing=8, height=0):
            for idx, weed in enumerate(self.field._cfg.weeds):
                with ui.CollapsableFrame(f"Species: {weed.name}", collapsed=False):
                    with ui.VStack(spacing=4, padding=5):

                        with ui.HStack(spacing=10):
                            ui.Label("Name", width=ui.Fraction(0.2), 
                                     tooltip="The species name. Used to organize the USD stage and as a {name} variable in paths.")
                            w_name = ui.StringField(width=ui.Fraction(0.8), 
                                     tooltip="The species name. Used to organize the USD stage and as a {name} variable in paths.")
                            w_name.model.set_value(weed.name)

                            def on_name_changed(m, w=weed):
                                setattr(w, "name", m.as_string)
                                self.field._cfg.save_to_settings()
                                if self._weeds_frame:
                                    self._weeds_frame.rebuild()

                            w_name.model.add_end_edit_fn(on_name_changed)
                    


                        with ui.HStack(spacing=10):
                            ui.Label("Semantic label", width=ui.Fraction(0.2), 
                                     tooltip="The specific class name for this species used by Replicator (e.g., 'Broadleaf' vs 'Grass').")
                            w_sem = ui.StringField(width=ui.Fraction(0.3), 
                                     tooltip="The specific class name for this species used by Replicator (e.g., 'Broadleaf' vs 'Grass').")
                            w_sem.model.set_value(str(weed.semantic_label))
                            w_sem.model.add_end_edit_fn(lambda m, w=weed: (setattr(w, "semantic_label", m.as_string), self.field._cfg.save_to_settings()))

                            ui.Label("Density", width=ui.Fraction(0.2), 
                                     tooltip="Target number of weeds per square meter of the field surface.")
                            dens = ui.FloatDrag(min=0, step=0.1, width=ui.Fraction(0.3), 
                                     tooltip="Target number of weeds per square meter of the field surface.")
                            dens.model.set_value(weed.density)
                            dens.model.add_value_changed_fn(lambda m, w=weed: (setattr(w, "density", m.as_float), self.field._cfg.save_to_settings()))

                            
                        
                        with ui.HStack(spacing=10):
                            ui.Label("Model Count", width=ui.Fraction(0.2), tooltip="Maximum number of model variations available in the folder.")
                            count_f = ui.IntField(width=ui.Fraction(0.3))
                            count_f.model.set_value(weed.model_count)
                            count_f.model.add_value_changed_fn(lambda m, w=weed: (setattr(w, "model_count", m.as_int), self.field._cfg.save_to_settings()))

                            ui.Label("Force unique", width=ui.Fraction(0.45))
                            force_unique = ui.CheckBox(width=ui.Fraction(0.05))
                            force_unique.model.set_value(weed.force_unique)  
                            force_unique.model.add_value_changed_fn(lambda m, w=weed: (setattr(w, "force_unique", m.as_bool), self.field._cfg.save_to_settings()))


                        with ui.HStack(spacing=10):
                            ui.Label("Path", width=ui.Fraction(0.2), 
                                     tooltip="Relative path for weed models. Supports {name}, and {num} placeholders.")
                            w_path = ui.StringField(width=ui.Fraction(0.6), 
                                     tooltip="Relative path for weed models. Supports {name}, and {num} placeholders.")
                            w_path.model.set_value(weed.path_template)
                            w_path.model.add_end_edit_fn(lambda m, w=weed: (setattr(w, "path_template", m.as_string), self.field._cfg.save_to_settings()))
                            
                            ui.Button("Remove", width=ui.Fraction(0.2), clicked_fn=lambda i=idx: self._remove_weed(i),
                                     tooltip="Delete this weed species configuration.")

            ui.Button("Add New Weed Type", clicked_fn=self._on_click_add_weed, height=20, 
                      tooltip="Add a new weed species to the field generator.")
        


    def _set_width(self, v: int):
        self.field._cfg.width = max(0.1, float(v))
        self._update_ground()
        self.field._cfg.save_to_settings()

    def _set_length(self, v: int):
        self.field._cfg.length = max(0.1, float(v))
        self._update_ground()
        self.field._cfg.save_to_settings()


    def _update_ground(self):
        """Apply current config width/length to /World/Ground scale."""
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath("/World/Field/Ground")
        if not prim.IsValid():
            return

        xform = UsdGeom.Xformable(prim)

        # Reuse an existing Scale op if there is one; otherwise create one.
        scale_op = None
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeScale:
                scale_op = op
                break
        if scale_op is None:
            scale_op = xform.AddScaleOp() 


        scale_op.Set((float(self.field._cfg.width), float(self.field._cfg.length), 0.01))

    def _on_click_add_bed(self):
        self.field._cfg.beds.append(BedConfig(name=f"New Bed {len(self.field._cfg.beds)+1}"))
        self._beds_frame.rebuild()
        self.field._cfg.save_to_settings()

    def _remove_bed(self, idx):
        self.field._cfg.beds.pop(idx)
        self._beds_frame.rebuild()
        self.field._cfg.save_to_settings()


    def _on_click_add_weed(self):
        self.field._cfg.weeds.append(WeedConfig(name="New_Weed", density=5.0))
        self._weeds_frame.rebuild()
        self.field._cfg.save_to_settings()

    def _remove_weed(self, idx):
        self.field._cfg.weeds.pop(idx)
        self._weeds_frame.rebuild()
        self.field._cfg.save_to_settings()

    def on_menu_callback(self):
        """Called when menu item is toggled"""
        pass

    def on_timeline_event(self, event):
        """Handle timeline events (Play, Pause, Reset)"""
        pass

    def on_physics_step(self, step: float):
        """Handle physics simulation steps"""
        pass

    def on_stage_event(self, event):
        """Handle stage events (Save, Open, Prim Selection)"""
        pass

    def cleanup(self):
        """Called when UI is closed or extension is shut down"""
        self._my_button = None
        self._my_slider = None


    def randomize_camera(self):
        cfg = self.field._cfg
        stage = omni.usd.get_context().get_stage()
        cam_prim = stage.GetPrimAtPath(cfg.camera_prim_path)
        
        if not cam_prim.IsValid():
            carb.log_warn(f"Can't find camera at {cfg.camera_prim_path}")
            return

        xform = UsdGeom.Xformable(cam_prim)
        
        z = random.uniform(*cfg.camera_height_range)
        x = random.uniform(*cfg.camera_offset_range)
        y = random.uniform(*cfg.camera_offset_range)
        
        t_op = next((op for op in xform.GetOrderedXformOps() if op.GetOpType() == UsdGeom.XformOp.TypeTranslate), None) or xform.AddTranslateOp()
        t_op.Set(Gf.Vec3d(x, y, z))

        tilt_x = random.uniform(*cfg.camera_tilt_range)
        tilt_y = random.uniform(*cfg.camera_tilt_range)
        
        r_op = next((op for op in xform.GetOrderedXformOps() if op.GetOpName() == "xformOp:rotateXYZ"), None) or xform.AddRotateXYZOp()
        r_op.Set(Gf.Vec3f(tilt_x, tilt_y, 0.0))

    
    def _validate_capture_state(self):
        """Checks if Replicator can safely run. Only affects the Capture button."""
        errors = []
        cfg = self.field._cfg
        stage = omni.usd.get_context().get_stage()

        for path_attr, label in [
            ("lights_parent_path", "Lights Parent"),
            ("materials_parent_path", "Materials Parent")
        ]:
            val = getattr(cfg, path_attr)
            if not val or not stage.GetPrimAtPath(val).IsValid():
                errors.append(f"{label} prim path is invalid/missing in Stage.")
        if not cfg.beds:
            errors.append("No beds defined.")
        else:
            for i, bed in enumerate(cfg.beds):
                if not bed.path_template:
                    errors.append(f"Bed {i+1} has no path template.")

        out_dir = cfg.output_directory
        if not out_dir or out_dir.strip() == "":
            errors.append("Output directory is not set.")
        else:
            parent_dir = os.path.dirname(out_dir.rstrip("/"))
            if not os.path.exists(parent_dir):
                errors.append(f"Output path is unreachable: {parent_dir} does not exist.")
        if errors:
            self._capture_btn.enabled = False
            self._warning_label.text = f"Capture Disabled: {errors[0]}"
            self._warning_label.visible = True
        else:
            self._capture_btn.enabled = True
            self._warning_label.visible = False

    def _ensure_replicator(self, res=(1024, 1024)):
        import omni.replicator.core as rep
        cfg = self.field._cfg
        stage = omni.usd.get_context().get_stage()
        cam_path = Sdf.Path(cfg.camera_prim_path)

        
        prim = stage.GetPrimAtPath(cam_path)
        if not prim.IsValid():
            # Create a top-down camera automatically
            cam = UsdGeom.Camera.Define(stage, cam_path)
            xform = UsdGeom.Xformable(cam.GetPrim())
            # Position 5 meters up, looking down
            m = Gf.Matrix4d().SetTranslate(Gf.Vec3d(0, 0, 5.0))
            xform.AddTransformOp().Set(m)
            carb.log_info(f"Created automatic camera at {cam_path}")

        self._render_product = rep.create.render_product(str(cam_path), res)
        self._writer = rep.WriterRegistry.get("BasicWriter")
        self._writer.initialize(output_dir=cfg.output_directory, rgb=True, semantic_segmentation=True, bounding_box_2d_tight=True )
        self._writer.attach([self._render_product])
        return True


    async def _capture_async(self, n: int, base_seed: int, delay_sec: float = 1.0):
        import time
        import omni.replicator.core as rep
        self._ensure_replicator()

        app = omni.kit.app.get_app()
        total_start = time.perf_counter()

        try:
            for i in range(n):
                if self._cancel_requested: break

                frame_start = time.perf_counter()
                seed = base_seed + i

                self.field._on_seed_update(seed)
                self.randomize_camera()
                rep.set_global_seed(seed)

                for _ in range(3):
                    await app.next_update_async()

                # Trigger capture
                await rep.orchestrator.step_async()
                
                if self._progress_bar:
                    self._progress_bar.model.set_value((i + 1) / n)
                if self._progress_label:
                    self._progress_label.text = f"Saved Frame {i+1}/{n}"
                carb.log_info(f"[Replicator] Frame {i+1}/{n} saved.")

        finally:
            rep.orchestrator.stop()
            # Final frames to ensure file handles are closed
            for _ in range(5):
                await app.next_update_async()
            
            self._capture_btn.enabled = True
            self._cancel_btn.enabled = False

            if self._progress_bar and self._cancel_requested:
                self._progress_bar.visible = False



    def _on_click_capture(self):
        n = self.field._cfg.capture_frames
        out = self.field._cfg.output_directory
        self._output_dir = out

        if self._writer and getattr(self._writer, "_output_dir", None) != out:
            self._writer = None
            self._render_product = None

        base_seed = int(self._seed_slider.model.as_int)

        self._cancel_requested = False
        self._capture_btn.enabled = False
        self._cancel_btn.enabled = True

        if self._progress_bar:
            self._progress_bar.visible = True
            self._progress_bar.model.set_value(0.0)
        if self._progress_label:
            self._progress_label.text = "Initializing Replicator..."

        asyncio.ensure_future(self._capture_async(n, base_seed))


