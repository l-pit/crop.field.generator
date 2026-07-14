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

import os
import numpy as np
import carb
from PIL import Image


def get_custom_y_offsets(user_formula: str, x_array: np.ndarray):
    safe_namespace = {
        "x": x_array,
        "sin": np.sin,
        "cos": np.cos,
        "tan": np.tan,
        "pi": np.pi,
        "abs": np.abs,
        "sqrt": np.sqrt,
        "rand": lambda: np.random.uniform(-1.0, 1.0),
        "noise": lambda: np.random.uniform(-1.0, 1.0, size=x_array.shape),
        "__builtins__": None
    }

    try:
        # eval is sandboxed: builtins are disabled and only the whitelisted
        # math/random names in safe_namespace are exposed to the expression.
        y_offsets = eval(user_formula, {"__builtins__": None}, safe_namespace)

        if isinstance(y_offsets, (int, float)):
            y_offsets = np.full_like(x_array, y_offsets)

        return y_offsets

    except SyntaxError:
        carb.log_error(f"Syntax Error in math formula: {user_formula}")
        return np.zeros_like(x_array)
    except NameError as e:
        carb.log_error(f"Invalid math function used: {e}")
        return np.zeros_like(x_array)
    except Exception as e:
        carb.log_error(f"Failed to parse formula: {e}")
        return np.zeros_like(x_array)


class TerrainGenerator:
    """Builds the ground height-field and provides fast height lookups for
    placing assets on the terrain. The final surface is composed of a base
    layer (flat, heightmap, or procedural noise) plus optional additive
    crop-terrain layers (ridge/furrow, anisotropic noise, clods). Pure
    NumPy/SciPy math, no USD dependencies."""

    def __init__(self, cfg):
        self._cfg = cfg
        self._current_terrain_heights = None

    def generate(self, field_width: float, field_length: float, z: float = 0.0):
        generate_uneven = getattr(self._cfg, 'generate_uneven_ground', False)
        simulate_ridge_furrow = getattr(self._cfg, 'simulate_ridge_furrow', False)
        aniso_enabled = getattr(self._cfg, 'aniso_noise_enabled', False)
        clods_enabled = getattr(self._cfg, 'clods_enabled', False)

        any_layer = (generate_uneven or simulate_ridge_furrow or aniso_enabled
                     or clods_enabled)

        if not any_layer:
            noise_array = np.zeros((2, 2))

        else:
            if generate_uneven:
                image_path = getattr(self._cfg, 'terrain_image_path', None)

                if image_path and os.path.exists(image_path):
                    carb.log_info(f"Generating terrain from image: {image_path}")
                    noise_array = self._read_heightmap_to_noise(
                        image_path=image_path,
                        field_width=field_width,
                        field_length=field_length
                    )
                else:
                    carb.log_info("Generating procedural noise terrain.")
                    noise_array = self._generate_procedural_noise(
                        field_width=field_width,
                        field_length=field_length
                    )
            else:
                noise_array = self._generate_zero_terrain(
                    field_width=field_width,
                    field_length=field_length
                )

            if simulate_ridge_furrow:
                carb.log_info("Adding crop-aligned ridge/furrow terrain.")
                noise_array += self._generate_ridge_furrow_layer(
                    field_width=field_width,
                    field_length=field_length
                )

            if aniso_enabled:
                carb.log_info("Adding anisotropic surface noise layer.")
                noise_array += self._generate_anisotropic_noise(
                    field_width=field_width,
                    field_length=field_length
                )

            if clods_enabled:
                carb.log_info("Adding clods (aggregate-scale bumps).")
                noise_array += self._generate_clods_layer(
                    field_width=field_width,
                    field_length=field_length
                )

        noise_array += z

        self._current_terrain_heights = noise_array

        return noise_array

    def _terrain_grid_shape(self, field_width: float, field_length: float):
        resolution = max(1.0, float(getattr(self._cfg, 'noise_resolution', 20)))
        target_rows = max(int(round(field_length * resolution)), 2)
        target_cols = max(int(round(field_width * resolution)), 2)
        return target_rows, target_cols

    def _generate_zero_terrain(self, field_width: float, field_length: float):
        rows, cols = self._terrain_grid_shape(field_width, field_length)
        return np.zeros((rows, cols), dtype=np.float32)

    def _cosine_band_weights(self, samples: np.ndarray, start: float, end: float, fade: float):
        weights = np.zeros_like(samples, dtype=np.float32)
        if end <= start:
            return weights

        inside = (samples >= start) & (samples <= end)
        weights[inside] = 1.0

        if fade <= 0.0:
            return weights

        left = (samples >= start - fade) & (samples < start)
        if np.any(left):
            t = np.clip((samples[left] - (start - fade)) / fade, 0.0, 1.0)
            weights[left] = 0.5 - 0.5 * np.cos(np.pi * t)

        right = (samples > end) & (samples <= end + fade)
        if np.any(right):
            t = np.clip((samples[right] - end) / fade, 0.0, 1.0)
            weights[right] = 0.5 + 0.5 * np.cos(np.pi * t)

        return weights.astype(np.float32, copy=False)

    def _generate_ridge_furrow_layer(self, field_width: float, field_length: float):
        """Crop-row-aligned ridge/furrow micro-terrain.

        For each bed we place a smooth crest at every crop row (matching the
        positions that ``_get_bed_row_positions`` uses) and a smooth trough at
        the midpoint between adjacent rows. The shape of each individual ridge
        is bounded by ``row_spacing/2`` so every selected row gets a proper
        crest regardless of ``bed_width``. Beds are merged with ``np.maximum``
        so overlapping or back-to-back beds always show their ridges.

        On top of the geometric ridge a couple of small modulations are added
        for early-corn realism:
          * along-row sinusoidal "planter mark" at ``plant_distance`` period
          * slow per-row crest variation (~15%) so ridges are not pencil-flat
        These are tiny (a few mm) and only act on top of the ridge band, so the
        overall amplitude stays within ``ridge_height`` / ``furrow_depth``.
        """
        ridge_height = max(0.0, float(getattr(self._cfg, 'ridge_height', 0.04)))
        furrow_depth = max(0.0, float(getattr(self._cfg, 'furrow_depth', 0.03)))
        steepness = max(0.1, float(getattr(self._cfg, 'ridge_steepness', 1.0)))
        micro_strength = max(0.0, float(getattr(self._cfg, 'ridge_micro_strength', 1.0)))
        if not self._cfg.beds or (ridge_height <= 0.0 and furrow_depth <= 0.0):
            return self._generate_zero_terrain(field_width, field_length)

        rows, cols = self._terrain_grid_shape(field_width, field_length)
        x_coords = np.linspace(-field_width / 2, field_width / 2, cols, dtype=np.float32)
        y_coords = np.linspace(-field_length / 2, field_length / 2, rows, dtype=np.float32)

        # Baseline: everywhere is in a furrow. Each per-row bump lifts the
        # surface up by `ridge_height + furrow_depth` at the row centerline.
        ridge_layer = np.full((rows, cols), -furrow_depth, dtype=np.float32)

        total_beds_width = sum(bed.bed_width for bed in self._cfg.beds)
        current_bed_x_start = -(total_beds_width / 2.0)

        # Y headland mask: ridges/furrows exist ONLY inside the planted region
        # (matches `_get_bed_row_positions`, which uses `length - 2*edge_width`).
        # The fade is applied INWARD, inside the planted region, so the ridge
        # ends exactly where the crops end -- no bleed into the headland.
        active_length = max(field_length - (self._cfg.edge_width * 2.0), 0.0)
        y_active_start = -active_length / 2.0
        y_active_end = active_length / 2.0
        # Short inward fade so the ridge eases out instead of cliffing off,
        # but stays bounded by the planted region.
        inward_fade = min(0.25, active_length / 4.0)
        y_mask = self._cosine_band_weights(
            y_coords,
            y_active_start + inward_fade,
            y_active_end - inward_fade,
            inward_fade,
        )

        amplitude = ridge_height + furrow_depth  # peak-to-trough swing

        for bed in self._cfg.beds:
            if bed.rows <= 0 or bed.row_spacing <= 0.0 or bed.bed_width <= 0.0:
                current_bed_x_start += bed.bed_width
                continue

            bed_center_x = current_bed_x_start + (bed.bed_width / 2.0)

            base_row_centers = np.linspace(
                bed_center_x - ((bed.rows - 1) * bed.row_spacing) / 2.0,
                bed_center_x + ((bed.rows - 1) * bed.row_spacing) / 2.0,
                bed.rows,
                dtype=np.float32,
            )

            # Optional per-bed Y-dependent row offset (curved rows).
            row_offsets = np.zeros_like(y_coords, dtype=np.float32)
            if getattr(bed, 'math_formula', ""):
                computed = np.asarray(
                    get_custom_y_offsets(bed.math_formula, y_coords),
                    dtype=np.float32,
                )
                if computed.shape != y_coords.shape:
                    computed = np.broadcast_to(computed, y_coords.shape).astype(np.float32, copy=False)
                row_offsets = computed

            half_spacing = max(float(bed.row_spacing) / 2.0, 1e-4)
            row_centers = base_row_centers[None, :] + row_offsets[:, None]  # (rows_y, n_rows)

            # Distance from each grid cell to the nearest crop row of this bed,
            # normalized by half_spacing and clipped at 1 (so the bump from one
            # row dies out exactly at the midpoint to its neighbour).
            normalized_dist = np.min(
                np.abs(x_coords[None, :, None] - row_centers[:, None, :]) / half_spacing,
                axis=2,
            )
            normalized_dist = np.clip(normalized_dist, 0.0, 1.0).astype(np.float32, copy=False)

            # Cosine bump: 1 at the row centerline, 0 at +-half_spacing.
            bump = 0.5 * (1.0 + np.cos(np.pi * normalized_dist)).astype(np.float32, copy=False)
            # Steepness: raising the bump to a power keeps endpoints fixed
            # (0 -> 0, 1 -> 1) but sharpens the crest. steepness == 1 gives the
            # default soft cosine; higher values give a more pointy ridge.
            if abs(steepness - 1.0) > 1e-6:
                bump = np.power(bump, steepness, dtype=np.float32)

            # Per-row slow crest variation (~15%) along the row direction so
            # ridges are not perfectly uniform. Wavelength ~ 8 row spacings.
            crest_wave = 1.0 + (0.15 * micro_strength) * np.sin(
                2.0 * np.pi * y_coords / max(8.0 * bed.row_spacing, 1e-3)
                + float(hash(bed.name) % 1000) * 0.001
            ).astype(np.float32)
            bump *= crest_wave[:, None]

            # Planter / seed-opener marks: small along-row sinusoid concentrated
            # on the ridge crest, period = plant_distance.
            plant_distance = max(float(getattr(bed, 'plant_distance', 0.0)), 0.0)
            if plant_distance > 1e-3 and micro_strength > 0.0:
                planter = (0.15 * micro_strength * ridge_height) * np.sin(
                    2.0 * np.pi * y_coords / plant_distance
                ).astype(np.float32)
                # Only modulate where the bump is strong (near the crest).
                bump_contrib = (-furrow_depth + amplitude * bump) + planter[:, None] * bump
            else:
                bump_contrib = -furrow_depth + amplitude * bump

            # Merge with the running layer; ridges always dominate furrows.
            ridge_layer = np.maximum(ridge_layer, bump_contrib.astype(np.float32, copy=False))

            current_bed_x_start += bed.bed_width

        # Headland fade: blend the ridge/furrow layer toward 0 (flat) outside
        # the planted region instead of leaving a sharp step.
        ridge_layer *= y_mask[:, None]
        return ridge_layer

    def _generate_anisotropic_noise(self, field_width: float, field_length: float):
        """Directional surface roughness: rougher across rows than along them.

        White noise is smoothed with a separable Gaussian whose sigma is much
        larger along Y (rows direction) than along X (cross-row). The result
        is normalised to the requested RMS amplitude.

        Combine freely with the isotropic procedural noise: this method
        returns a layer to be ADDED to the existing ``noise_array``.
        """
        import scipy.ndimage

        amplitude = max(0.0, float(getattr(self._cfg, 'aniso_amplitude', 0.012)))
        if amplitude <= 0.0:
            return self._generate_zero_terrain(field_width, field_length)

        rows, cols = self._terrain_grid_shape(field_width, field_length)
        resolution = max(1.0, float(getattr(self._cfg, 'noise_resolution', 20)))
        sigma_along_m = max(0.01, float(getattr(self._cfg, 'aniso_smooth_along_m', 0.40)))
        sigma_across_m = max(0.01, float(getattr(self._cfg, 'aniso_smooth_across_m', 0.10)))
        # sigma in pixels = metres * pixels-per-metre
        sigma_y = sigma_along_m * resolution
        sigma_x = sigma_across_m * resolution

        white = np.random.uniform(-1.0, 1.0, (rows, cols)).astype(np.float32)
        smoothed = scipy.ndimage.gaussian_filter(white, sigma=(sigma_y, sigma_x))

        # Normalise to amplitude in metres (RMS)
        rms = float(np.sqrt(np.mean(smoothed * smoothed)))
        if rms < 1e-9:
            return self._generate_zero_terrain(field_width, field_length)
        return (smoothed * (amplitude / rms)).astype(np.float32, copy=False)

    def _generate_clods_layer(self, field_width: float, field_length: float):
        """Discrete soil aggregates: small Gaussian bumps scattered uniformly.

        Implemented as a vectorised additive composite — for each clod we only
        touch a small bounding box (±3 sigma) so total cost is
        O(n_clods * patch_pixels²) which stays cheap at default density.
        """
        if not getattr(self._cfg, 'clods_enabled', False):
            return self._generate_zero_terrain(field_width, field_length)

        density = max(0.0, float(getattr(self._cfg, 'clod_density', 5.0)))
        if density <= 0.0:
            return self._generate_zero_terrain(field_width, field_length)

        r_min = max(1e-3, float(getattr(self._cfg, 'clod_min_radius', 0.02)))
        r_max = max(r_min, float(getattr(self._cfg, 'clod_max_radius', 0.05)))
        h_min = max(0.0, float(getattr(self._cfg, 'clod_min_height', 0.005)))
        h_max = max(h_min, float(getattr(self._cfg, 'clod_max_height', 0.015)))

        rows, cols = self._terrain_grid_shape(field_width, field_length)
        H = np.zeros((rows, cols), dtype=np.float32)
        x = np.linspace(-field_width / 2.0, field_width / 2.0, cols, dtype=np.float32)
        y = np.linspace(-field_length / 2.0, field_length / 2.0, rows, dtype=np.float32)
        dx = (x[1] - x[0]) if cols > 1 else 1.0
        dy = (y[1] - y[0]) if rows > 1 else 1.0

        n_clods = int(round(density * field_width * field_length))
        if n_clods <= 0:
            return H

        cx = np.random.uniform(-field_width / 2.0, field_width / 2.0, n_clods).astype(np.float32)
        cy = np.random.uniform(-field_length / 2.0, field_length / 2.0, n_clods).astype(np.float32)
        radii = np.random.uniform(r_min, r_max, n_clods).astype(np.float32)
        heights = np.random.uniform(h_min, h_max, n_clods).astype(np.float32)

        for i in range(n_clods):
            r = float(radii[i])
            half = 3.0 * r
            # Convert clod centre back to grid indices
            ix0 = max(0, int(np.floor((cx[i] - half - x[0]) / dx)))
            ix1 = min(cols, int(np.ceil((cx[i] + half - x[0]) / dx)) + 1)
            iy0 = max(0, int(np.floor((cy[i] - half - y[0]) / dy)))
            iy1 = min(rows, int(np.ceil((cy[i] + half - y[0]) / dy)) + 1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            sx = (x[ix0:ix1] - cx[i]) / r
            sy = (y[iy0:iy1] - cy[i]) / r
            bump = float(heights[i]) * np.exp(-(sx[None, :] ** 2 + sy[:, None] ** 2))
            H[iy0:iy1, ix0:ix1] += bump.astype(np.float32, copy=False)

        return H

    def _read_heightmap_to_noise(self, image_path: str, field_width: float, field_length: float):
        """
        Reads a heightmap, normalizes it, and loops (tiles) it to fit the physical field
        at the specified resolution (pixels per meter).
        """

        max_height = getattr(self._cfg, 'terrain_max_height', 0.2)

        # rows = Y axis = along-row direction = length
        # cols = X axis = cross-row direction = width
        target_rows, target_cols = self._terrain_grid_shape(field_width, field_length)

        fallback_array = np.zeros((target_rows, target_cols), dtype=np.float32)

        if not os.path.exists(image_path):
            carb.log_error(f"Heightmap image not found: {image_path}")
            return fallback_array

        try:
            img = Image.open(image_path).convert('L')
            base_array = np.array(img) / 255.0 * max_height
            img_rows, img_cols = base_array.shape

            y_double = np.arange(target_rows) % (img_rows * 2)
            x_double = np.arange(target_cols) % (img_cols * 2)

            y_indices = np.minimum(y_double, (img_rows * 2 - 1) - y_double)
            x_indices = np.minimum(x_double, (img_cols * 2 - 1) - x_double)

            tiled_array = base_array[np.ix_(y_indices, x_indices)]

            return tiled_array

        except Exception as e:
            carb.log_error(f"Failed to process heightmap: {e}")
            return fallback_array

    def _generate_procedural_noise(self, field_width: float, field_length: float):
        import scipy.ndimage

        resolution = max(1.0, float(getattr(self._cfg, 'noise_resolution', 20)))
        scale = getattr(self._cfg, 'terrain_scale', 1.0)
        octaves = getattr(self._cfg, 'terrain_octaves', 4)
        persistence = getattr(self._cfg, 'terrain_persistence', 0.5)
        lacunarity = getattr(self._cfg, 'terrain_lacunarity', 2.0)
        max_height = getattr(self._cfg, 'terrain_max_height', 0.2)

        # rows = Y axis = along-row direction = length
        # cols = X axis = cross-row direction = width
        target_rows, target_cols = self._terrain_grid_shape(field_width, field_length)

        noise_map = np.zeros((target_rows, target_cols), dtype=np.float32)

        amplitude = 1.0
        frequency = 1.0
        max_amplitude_sum = 0.0

        for _ in range(octaves):
            grid_rows = max(2, int(target_rows / (scale * resolution) * frequency))
            grid_cols = max(2, int(target_cols / (scale * resolution) * frequency))

            random_grid = np.random.uniform(-1.0, 1.0, (grid_rows, grid_cols))

            zoom_y = target_rows / grid_rows
            zoom_x = target_cols / grid_cols
            octave_noise = scipy.ndimage.zoom(random_grid, (zoom_y, zoom_x), order=3)

            octave_noise = octave_noise[:target_rows, :target_cols]

            noise_map += octave_noise * amplitude

            max_amplitude_sum += amplitude
            amplitude *= persistence
            frequency *= lacunarity

        normalized_map = (noise_map / max_amplitude_sum + 1.0) / 2.0
        final_height_array = (normalized_map * max_height).astype(np.float32, copy=False)

        return final_height_array

    def sample_height(self, x_usd: float, y_usd: float) -> float:
        """Instantly looks up the Z height on the terrain for any physical X/Y coordinate."""
        if self._current_terrain_heights is None:
            return 0.0

        rows, cols = self._current_terrain_heights.shape

        x_pct = (x_usd + (self._cfg.width / 2)) / self._cfg.width
        col_idx = int(np.clip(x_pct * (cols - 1), 0, cols - 1))

        y_pct = (y_usd + (self._cfg.length / 2)) / self._cfg.length
        row_idx = int(np.clip(y_pct * (rows - 1), 0, rows - 1))

        return float(self._current_terrain_heights[row_idx, col_idx])
