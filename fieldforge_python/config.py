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

from dataclasses import dataclass, field
from typing import List
import carb.settings
from itertools import count
import dataclasses
import os

# Absolute path to the sample models bundled with the extension, resolved from
# this file's location so the default paths work wherever the repo is installed.
_SAMPLE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sample_models"
)

def _sample(*parts):
    """Build an absolute path template into the bundled sample models.
    The literal ``{num...}`` placeholders are preserved for later .format()."""
    return os.path.join(_SAMPLE_ROOT, *parts)

@dataclass
class BedConfig:
    name: str = "Bed"
    semantic_label: str = "Corn"
    model_count: int = 100
    rows: int = 2
    path_template: str = "" 
    bed_width: float = 0.5
    row_spacing: float = 0.3
    skip_chance: float = 0.1
    plant_jitter: float = 0.04
    plant_distance: float = 0.08
    force_unique: bool = False
    math_formula: str = ""

@dataclass
class WeedConfig:
    name: str = "Weed Type"
    model_count: int = 50
    semantic_label: str = "Weed"
    density: float = 5.0
    path_template: str = "" 
    force_unique: bool = True

@dataclass
class FieldConfig:
    width: float = 0.8
    length: float = 0.8
    edge_width: float = 0.1
    beds: List[BedConfig] = field(default_factory=list)
    weeds: List[WeedConfig] = field(default_factory=list)

    field_prim_path: str = "/World/Field"

    lights_parent_path: str = "/World/Lights"
    materials_parent_path: str = "/World/Looks"

    generate_uneven_ground: bool = False
    terrain_image_path: str = ""

    noise_resolution: float = 30
    terrain_max_height: float = 0.2
    terrain_scale: float = 1.2
    terrain_octaves: int = 4
    terrain_persistence: float = 0.5
    terrain_lacunarity: float = 2.0
    simulate_ridge_furrow: bool = False
    ridge_height: float = 0.04
    furrow_depth: float = 0.03
    ridge_steepness: float = 1.0
    ridge_micro_strength: float = 1.0

    # Layer 5 — Anisotropic surface noise (additive on top of isotropic)
    aniso_noise_enabled: bool = False
    aniso_amplitude: float = 0.012        # m, RMS
    aniso_smooth_along_m: float = 0.40    # sigma along Y (rows direction) in metres
    aniso_smooth_across_m: float = 0.10   # sigma along X (across rows) in metres

    # Layer 6 — Clods (aggregate-scale bumps)
    clods_enabled: bool = False
    clod_density: float = 5.0             # clods per m²
    clod_min_radius: float = 0.02         # m
    clod_max_radius: float = 0.05         # m
    clod_min_height: float = 0.005        # m
    clod_max_height: float = 0.015        # m

    light_x_range: tuple = (0.0, 0.0)
    light_y_range: tuple = (-30.0, 30.0)
    light_z_range: tuple = (0.0, 360.0)
    
    _SETTING_PATH = "/persistent/fieldforge/last_config"

    use_point_instancer: bool = False

    capture_frames: int = 5
    output_directory: str = "" 
    camera_prim_path: str = "/World/FieldCamera"

    camera_height_range: tuple = (2.0, 3.0) 
    camera_offset_range: tuple = (-0.2, 0.2) 
    camera_tilt_range: tuple = (-5.0, 5.0)    

    save_rgb: bool = True
    save_semantic: bool = True
    save_bbox_2d: bool = False
    save_depth: bool = False
    save_instance: bool = False

    def __post_init__(self):

        success = self.load_from_settings()
        
        if not success:
            if not self.beds:
                self.beds = [
                    BedConfig(
                        name="Corn",
                        semantic_label="corn",
                        rows=1,
                        model_count=10,
                        path_template=_sample(
                            "Corn", "V2", "corn_v2_{num:02d}", "corn_v2_{num:02d}.usd"
                        ),
                    )
                ]
            if not self.weeds:
                self.weeds = [
                    WeedConfig(
                        name="Black_Bindweed",
                        semantic_label="weed",
                        model_count=5,
                        density=8.0,
                        path_template=_sample(
                            "Weeds", "Black_Bindweed", "Black_Bindweed_Stage_1",
                            "black_bindweed_s1_{num:02d}", "black_bindweed_s1_{num:02d}.usd",
                        ),
                    ),
                    WeedConfig(
                        name="White_Quinoa",
                        semantic_label="weed",
                        model_count=5,
                        density=8.0,
                        path_template=_sample(
                            "Weeds", "White_Quinoa", "White_Quinoa_Stage_1",
                            "white_quinoa_s1_{num:02d}", "white_quinoa_s1_{num:02d}.usd",
                        ),
                    ),
                ]

    def save_to_settings(self):
        settings = carb.settings.get_settings()
         
        settings.destroy_item(self._SETTING_PATH)
        

        data = dataclasses.asdict(self)
        
        self._recursive_set_dict(settings, self._SETTING_PATH, data)
    
    def _recursive_set_dict(self, settings, path, data):
            """Recursively sets values in the Carb registry."""
            if isinstance(data, dict):
                for key, value in data.items():
                    self._recursive_set_dict(settings, f"{path}/{key}", value)
            elif isinstance(data, (list, tuple)):
                for i, item in enumerate(data):
                    self._recursive_set_dict(settings, f"{path}/{i}", item)
            # Base cases for leaf values
            elif isinstance(data, bool):
                settings.set_bool(path, data)
            elif isinstance(data, int):
                settings.set_int(path, data)
            elif isinstance(data, float):
                settings.set_float(path, data)
            elif isinstance(data, str):
                settings.set_string(path, data)

    def load_from_settings(self):
        settings = carb.settings.get_settings()

        def get_setting(path, default):
            val = settings.get(path)
            return val if val is not None else default
        
        if settings.get(f"{self._SETTING_PATH}/width") is None:
            return False

        self.width = get_setting(f"{self._SETTING_PATH}/width", self.width)
        self.length = get_setting(f"{self._SETTING_PATH}/length", self.length)
        self.edge_width = get_setting(f"{self._SETTING_PATH}/edge_width", self.edge_width)
        self.lights_parent_path = get_setting(f"{self._SETTING_PATH}/lights_parent_path", self.lights_parent_path)
        self.materials_parent_path = get_setting(f"{self._SETTING_PATH}/materials_parent_path", self.materials_parent_path)
        self.capture_frames = get_setting(f"{self._SETTING_PATH}/capture_frames", self.capture_frames)
        self.output_directory = get_setting(f"{self._SETTING_PATH}/output_directory", self.output_directory)
        self.field_prim_path = get_setting(f"{self._SETTING_PATH}/field_prim_path", self.field_prim_path)
        self.use_point_instancer = get_setting(f"{self._SETTING_PATH}/use_point_instancer", self.use_point_instancer) 

        self.generate_uneven_ground = get_setting(f"{self._SETTING_PATH}/generate_uneven_ground", self.generate_uneven_ground) 
        self.terrain_image_path = get_setting(f"{self._SETTING_PATH}/terrain_image_path", self.terrain_image_path) 
        self.noise_resolution = get_setting(f"{self._SETTING_PATH}/noise_resolution", self.noise_resolution) 
        self.terrain_max_height = get_setting(f"{self._SETTING_PATH}/terrain_max_height", self.terrain_max_height) 
        self.terrain_scale = get_setting(f"{self._SETTING_PATH}/terrain_scale", self.terrain_scale) 
        self.terrain_octaves = get_setting(f"{self._SETTING_PATH}/terrain_octaves", self.terrain_octaves) 
        self.terrain_persistence = get_setting(f"{self._SETTING_PATH}/terrain_persistence", self.terrain_persistence) 
        self.terrain_lacunarity = get_setting(f"{self._SETTING_PATH}/terrain_lacunarity", self.terrain_lacunarity) 
        self.simulate_ridge_furrow = get_setting(f"{self._SETTING_PATH}/simulate_ridge_furrow", self.simulate_ridge_furrow)
        self.ridge_height = get_setting(f"{self._SETTING_PATH}/ridge_height", self.ridge_height)
        self.furrow_depth = get_setting(f"{self._SETTING_PATH}/furrow_depth", self.furrow_depth)
        self.ridge_steepness = get_setting(f"{self._SETTING_PATH}/ridge_steepness", self.ridge_steepness)
        self.ridge_micro_strength = get_setting(f"{self._SETTING_PATH}/ridge_micro_strength", self.ridge_micro_strength)

        # Anisotropic noise
        self.aniso_noise_enabled = get_setting(f"{self._SETTING_PATH}/aniso_noise_enabled", self.aniso_noise_enabled)
        self.aniso_amplitude = get_setting(f"{self._SETTING_PATH}/aniso_amplitude", self.aniso_amplitude)
        self.aniso_smooth_along_m = get_setting(f"{self._SETTING_PATH}/aniso_smooth_along_m", self.aniso_smooth_along_m)
        self.aniso_smooth_across_m = get_setting(f"{self._SETTING_PATH}/aniso_smooth_across_m", self.aniso_smooth_across_m)

        # Clods
        self.clods_enabled = get_setting(f"{self._SETTING_PATH}/clods_enabled", self.clods_enabled)
        self.clod_density = get_setting(f"{self._SETTING_PATH}/clod_density", self.clod_density)
        self.clod_min_radius = get_setting(f"{self._SETTING_PATH}/clod_min_radius", self.clod_min_radius)
        self.clod_max_radius = get_setting(f"{self._SETTING_PATH}/clod_max_radius", self.clod_max_radius)
        self.clod_min_height = get_setting(f"{self._SETTING_PATH}/clod_min_height", self.clod_min_height)
        self.clod_max_height = get_setting(f"{self._SETTING_PATH}/clod_max_height", self.clod_max_height)

        
        def load_range(path, default):
            if settings.get(f"{path}/0") is None:
                return default
            
            val0 = settings.get_as_float(f"{path}/0")
            val1 = settings.get_as_float(f"{path}/1")
            return (val0, val1)

        self.light_x_range = load_range(f"{self._SETTING_PATH}/light_x_range", self.light_x_range)
        self.light_y_range = load_range(f"{self._SETTING_PATH}/light_y_range", self.light_y_range)
        self.light_z_range = load_range(f"{self._SETTING_PATH}/light_z_range", self.light_z_range)

        self.camera_prim_path = get_setting(f"{self._SETTING_PATH}/camera_prim_path", self.camera_prim_path)

        self.camera_height_range = load_range(f"{self._SETTING_PATH}/camera_height_range", self.camera_height_range)
        self.camera_offset_range = load_range(f"{self._SETTING_PATH}/camera_offset_range", self.camera_offset_range)
        self.camera_tilt_range = load_range(f"{self._SETTING_PATH}/camera_tilt_range", self.camera_tilt_range)
        
        loaded_beds = []
        for i in count(): 
            bed_path = f"{self._SETTING_PATH}/beds/{i}"

            b_name = settings.get(f"{bed_path}/name")
            if b_name is None:
                break
                
            d_b = BedConfig()
            loaded_beds.append(BedConfig(
                name=b_name,
                semantic_label=get_setting(f"{bed_path}/semantic_label", d_b.semantic_label),
                model_count=get_setting(f"{bed_path}/model_count", d_b.model_count),
                rows=get_setting(f"{bed_path}/rows", d_b.rows),
                path_template=get_setting(f"{bed_path}/path_template", d_b.path_template),
                bed_width=get_setting(f"{bed_path}/bed_width", d_b.bed_width),
                row_spacing=get_setting(f"{bed_path}/row_spacing", d_b.row_spacing),
                skip_chance=get_setting(f"{bed_path}/skip_chance", d_b.skip_chance),
                plant_jitter=get_setting(f"{bed_path}/plant_jitter", d_b.plant_jitter),
                plant_distance=get_setting(f"{bed_path}/plant_distance", d_b.plant_distance),
                force_unique=get_setting(f"{bed_path}/force_unique", d_b.force_unique),
                math_formula=get_setting(f"{bed_path}/math_formula", d_b.math_formula),

            ))
        
        if loaded_beds:
            self.beds = loaded_beds

        loaded_weeds = []
        for i in count():
            weed_path = f"{self._SETTING_PATH}/weeds/{i}"
            w_name = settings.get(f"{weed_path}/name")
            if w_name is None:
                break

            d_w = WeedConfig()
            
            loaded_weeds.append(WeedConfig(
                name=w_name,
                model_count=get_setting(f"{weed_path}/model_count", d_w.model_count),
                semantic_label=get_setting(f"{weed_path}/semantic_label", d_w.semantic_label),
                density=get_setting(f"{weed_path}/density", d_w.density),
                path_template=get_setting(f"{weed_path}/path_template", d_w.path_template),
                force_unique=get_setting(f"{weed_path}/force_unique", d_w.force_unique)
            ))
        
        if loaded_weeds:
            self.weeds = loaded_weeds

        return True