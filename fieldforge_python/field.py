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

import random
import numpy as np
from .config import FieldConfig
from .terrain import TerrainGenerator, get_custom_y_offsets
import omni.kit.commands
from isaacsim.core.utils.stage import add_reference_to_stage
from pxr import Usd
import omni.usd
from isaacsim.core.utils.semantics import upgrade_prim_semantics_to_labels, add_labels
import os
import re
import carb
import asyncio


from pxr import UsdGeom, Gf, Sdf, UsdShade, Vt


 
class Field:
    def __init__(self):
        self._cfg = FieldConfig()
        self._cfg.load_from_settings()

        self._terrain = TerrainGenerator(self._cfg)

        self.stage = omni.usd.get_context().get_stage()
        self.field_prim_path = self._cfg.field_prim_path

    def _on_seed_update(self, _seed):
        random.seed(_seed)
        np.random.seed(_seed)

        self.stage = omni.usd.get_context().get_stage()

        root_layer = self.stage.GetRootLayer()
        self.stage.SetEditTarget(Usd.EditTarget(root_layer))

        self.field_prim_path = self._cfg.field_prim_path
        protected_paths = ["/", "/World", "/Library", "/Looks"]
        if self.field_prim_path in protected_paths:
            carb.log_error(f"Cannot spawn field at {self.field_prim_path}. Path is protected.")
            return

        field_prim = self.stage.GetPrimAtPath(self.field_prim_path)
        if field_prim and field_prim.IsValid():
            for child in field_prim.GetChildren():
                self.stage.RemovePrim(child.GetPath())
        else:
            UsdGeom.Xform.Define(self.stage, self.field_prim_path) 

        ground_prim = self.create_ground_plane(
            width=self._cfg.width,
            length=self._cfg.length,
            z=0.0,
            plane_path=f"{self.field_prim_path}/Terrain"
        )

        self._bind_random_material_to_ground(self._cfg.materials_parent_path, ground_prim)
        self.show_random_light(self._cfg.lights_parent_path)

        use_point_instancer = getattr(self._cfg, 'use_point_instancer', False) 

        if use_point_instancer:
            self._generate_with_point_instancer()
        else:
            self._generate_with_instanceables()



    def _generate_with_point_instancer(self):
        total_beds_width = sum(bed.bed_width for bed in self._cfg.beds)
        current_bed_y_start = - (total_beds_width / 2)

        for b_idx, bed in enumerate(self._cfg.beds):
            bed_center_y = current_bed_y_start + (bed.bed_width / 2)
            safe_bed_name = self.get_usd_safe_name(bed.name)
            instancer_path = f"{self.field_prim_path}/{safe_bed_name}_Instancer"
            
            positions, orientations, indices, prototype_paths = self._get_bed_instancer_data(bed, bed_center_y)
            
            if positions and len(positions) > 0:
                carb.log_info(f"Generating {bed.name} (PointInstancer: {len(positions)} items)...")
                self._create_point_instancer(
                    instancer_path=instancer_path,
                    positions=positions,
                    orientations=orientations,
                    proto_indices=indices,
                    prototype_usd_paths=prototype_paths,
                    semantic_label=bed.semantic_label
                )
            current_bed_y_start += bed.bed_width

        if self._cfg.weeds:
            for w_idx, weed in enumerate(self._cfg.weeds):
                instancer_path = f"{self.field_prim_path}/Weeds_{weed.name}_Instancer"
                positions, orientations, indices, prototype_paths = self._get_weeds_instancer_data(weed)
                
                if positions and len(positions) > 0:
                    carb.log_info(f"Generating weeds: {weed.name} (PointInstancer: {len(positions)} items)...")
                    self._create_point_instancer(
                        instancer_path=instancer_path,
                        positions=positions,
                        orientations=orientations,
                        proto_indices=indices,
                        prototype_usd_paths=prototype_paths,
                        semantic_label=weed.semantic_label
                    )
    
    def _generate_with_instanceables(self):
        z = 0.0
        total_beds_width = sum(bed.bed_width for bed in self._cfg.beds)
        current_bed_y_start = - (total_beds_width / 2)
        
        paths_to_lock = []

        for b_idx, bed in enumerate(self._cfg.beds):
            bed_center_y = current_bed_y_start + (bed.bed_width / 2)
            safe_bed_name = self.get_usd_safe_name(bed.name)
            
            bed_prim_path = f"{self.field_prim_path}/{safe_bed_name}"
            UsdGeom.Xform.Define(self.stage, bed_prim_path)
        
            row_positions = self._get_bed_row_positions(bed, bed_center_y)
            model_numbers = self._generate_model_numbers(len(row_positions), bed.model_count, bed.force_unique)
            
            carb.log_info(f"Generating {bed.name} (Instanceables: {len(row_positions)} items)...")

            for i, ((x, y), n) in enumerate(zip(row_positions, model_numbers), start=1):
                usd_path = bed.path_template.format(num=n)
                prim_path = f"{bed_prim_path}/plant_{i}" 
                z = self._terrain.sample_height(float(x), float(y))
                self._place_asset(usd_path, prim_path, x, y, z, semantic_label=bed.semantic_label)
                paths_to_lock.append(prim_path) 
            
            current_bed_y_start += bed.bed_width

        if self._cfg.weeds:
            weeds_root = f"{self.field_prim_path}/weeds"
            UsdGeom.Xform.Define(self.stage, weeds_root)
            
            for w_idx, weed in enumerate(self._cfg.weeds):
                weeds_positions = self._get_weeds_positions(weed.density)
                
                if weeds_positions is not None and len(weeds_positions) > 0:
                    model_numbers = self._generate_model_numbers(len(weeds_positions), weed.model_count, weed.force_unique)
                    
                    carb.log_info(f"Generating weeds: {weed.name} (Instanceables: {len(weeds_positions)} items)...")
                    
                    for i, ((x, y), n) in enumerate(zip(weeds_positions, model_numbers), start=1):
                        usd_path = weed.path_template.format(num=n, name=weed.name)
                        prim_path = f"{weeds_root}/{weed.name}_{i}"
                        z = self._terrain.sample_height(float(x), float(y))
                        self._place_asset(usd_path, prim_path, x, y, z, semantic_label=weed.semantic_label)
                        paths_to_lock.append(prim_path)

        if paths_to_lock:
            asyncio.ensure_future(self._lock_instances_next_frame(paths_to_lock))

    def _create_point_instancer(self, instancer_path, positions, orientations, proto_indices, prototype_usd_paths, semantic_label):
        instancer = UsdGeom.PointInstancer.Define(self.stage, instancer_path)
        instancer_prim = instancer.GetPrim()

        num_instances = len(positions)
        indices = list(range(num_instances))
        
        pv_api = UsdGeom.PrimvarsAPI(instancer_prim)

        
        instancer.CreateIdsAttr(indices)

        prototypes_scope_path = f"{instancer_path}/Prototypes"
        UsdGeom.Scope.Define(self.stage, prototypes_scope_path)
        
        proto_rel = instancer.GetPrototypesRel()
        from pxr import UsdSemantics
        for i, usd_path in enumerate(prototype_usd_paths):
            proto_path = f"{prototypes_scope_path}/proto_{i}"
            add_reference_to_stage(usd_path=usd_path, prim_path=proto_path)
            proto_prim = self.stage.GetPrimAtPath(proto_path)

            upgrade_prim_semantics_to_labels(proto_prim)

            labels_api = UsdSemantics.LabelsAPI.Apply(proto_prim, "class")
            labels_api.CreateLabelsAttr().Set([str(semantic_label)])
            
            proto_rel.AddTarget(Sdf.Path(proto_path))

        instancer.GetPositionsAttr().Set(positions)
        instancer.GetOrientationsAttr().Set(orientations)
        instancer.GetProtoIndicesAttr().Set(proto_indices)
    
    def create_terrain_from_noise(self, noise_array: np.ndarray, width: float, length: float, plane_path="/World/Field/Terrain"):
        """
        Converts a 2D numpy array of height values into a physical USD Mesh.
        """
        rows, cols = noise_array.shape
        mesh = UsdGeom.Mesh.Define(self.stage, plane_path)
        mesh.GetSubdivisionSchemeAttr().Set(UsdGeom.Tokens.catmullClark)

        x = np.linspace(-width/2, width/2, cols)
        y = np.linspace(-length/2, length/2, rows)
        xv, yv = np.meshgrid(x, y)


        vertices = np.stack([xv.ravel(), yv.ravel(), noise_array.ravel()], axis=-1)
        mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(vertices.astype(np.float32)))


        num_faces = (rows - 1) * (cols - 1)
        face_vertex_counts = np.full(num_faces, 4, dtype=np.int32)
        mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray.FromNumpy(face_vertex_counts))


        indices = np.zeros((rows - 1, cols - 1, 4), dtype=np.int32)

        r_idx, c_idx = np.indices((rows - 1, cols - 1))
        i = r_idx * cols + c_idx
        
        indices[..., 0] = i              # Top-Left
        indices[..., 1] = i + 1          # Top-Right
        indices[..., 2] = i + cols + 1   # Bottom-Right
        indices[..., 3] = i + cols       # Bottom-Left
        
        mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(indices.ravel()))

            
        from pxr import UsdPhysics
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
        mesh_collision = UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim())
        mesh_collision.CreateApproximationAttr().Set("none") # Use exact triangle mesh for physics

        return mesh.GetPrim()
    
    def _generate_model_numbers(self, total_needed: int, model_count: int, force_unique: bool):
        total_needed = int(total_needed)
        model_count = int(model_count)
        if not force_unique:
            return [random.randint(1, model_count) for _ in range(total_needed)]
        
        result = []
        base_deck = list(range(1, model_count + 1))
        
        while len(result) < total_needed:
            random.shuffle(base_deck)
            needed = total_needed - len(result)
            result.extend(base_deck[:needed])
            
        return result
    
    def _get_bed_instancer_data(self, bed, center_y):
        positions = []
        orientations = []
        raw_model_nums = []

        row_positions = self._get_bed_row_positions(bed, center_y)

        for pos in row_positions:

            x_val, y_val = float(pos[0]), float(pos[1])
            z_val = self._terrain.sample_height(x_val, y_val)
            positions.append(Gf.Vec3f(x_val, y_val, z_val))
            
            rot_z = random.uniform(0, 360.0)
            quat = Gf.Rotation(Gf.Vec3d(0, 0, 1), rot_z).GetQuat()
            orientations.append(Gf.Quath(float(quat.GetReal()), Gf.Vec3h(*quat.GetImaginary())))
            
            raw_model_nums.append(random.randint(1, int(bed.model_count)))
                
        unique_models = sorted(list(set(raw_model_nums)))
        

        prototype_usd_paths = [bed.path_template.format(num=n) for n in unique_models]
        

        model_to_idx = {model_num: idx for idx, model_num in enumerate(unique_models)}
        

        indices = [model_to_idx[num] for num in raw_model_nums]
                
        return Vt.Vec3fArray(positions), Vt.QuathArray(orientations), Vt.IntArray(indices), prototype_usd_paths


    def _get_weeds_instancer_data(self, weed):
        if not weed.density:
            return None, None, None, None

        available_width = self._cfg.width - (self._cfg.edge_width * 2)
        available_length = self._cfg.length - (self._cfg.edge_width * 2)
        n_points = int(available_width * available_length * weed.density)
        
        if n_points == 0:
            return None, None, None, None
            
        x_arr = np.random.uniform(0, available_width, n_points) - available_width / 2
        y_arr = np.random.uniform(0, available_length, n_points) - available_length / 2
        
        positions = []
        for x, y in zip(x_arr, y_arr):
            z_val = self._terrain.sample_height(float(x), float(y))
            positions.append(Gf.Vec3f(float(x), float(y), z_val))

        orientations = []
        for _ in range(n_points):

            rot_z = random.uniform(0, 360.0)
            quat = Gf.Rotation(Gf.Vec3d(0, 0, 1), rot_z).GetQuat()
            orientations.append(Gf.Quath(float(quat.GetReal()), Gf.Vec3h(*quat.GetImaginary())))


        raw_model_nums = [random.randint(1, int(weed.model_count)) for _ in range(n_points)]
        unique_models = sorted(list(set(raw_model_nums)))
        
        prototype_usd_paths = [weed.path_template.format(num=n, name=weed.name) for n in unique_models]
        model_to_idx = {model_num: idx for idx, model_num in enumerate(unique_models)}
        indices = [model_to_idx[num] for num in raw_model_nums]

        return Vt.Vec3fArray(positions), Vt.QuathArray(orientations), Vt.IntArray(indices), prototype_usd_paths
        
    def _place_asset(self, usd_path, prim_path, x, y, z, semantic_label):
        if not os.path.exists(usd_path):
            carb.log_warn(f"Asset not found: {usd_path}")
            return

        prim = self.stage.GetPrimAtPath(prim_path)
        
        if not prim.IsValid():
            prim = UsdGeom.Xform.Define(self.stage, prim_path).GetPrim()
            
        prim.SetInstanceable(False)
        prim.GetReferences().ClearReferences()
            
        prim.GetReferences().AddReference(usd_path)

        upgrade_prim_semantics_to_labels(prim)
        add_labels(prim, str(semantic_label))

        xformable = UsdGeom.Xformable(prim)
        xformable.ClearXformOpOrder() 
        
        t_op = xformable.AddTranslateOp()
        t_op.Set(Gf.Vec3d(float(x), float(y), float(z)))
        
        rZ_op = xformable.AddRotateZOp()
        rZ_op.Set(random.uniform(0, 360.0))
        
        rX_op = xformable.AddRotateXOp()
        rX_op.Set(90.0)
        

    async def _lock_instances_next_frame(self, paths):
        await omni.kit.app.get_app().next_update_async()
        
        stage = omni.usd.get_context().get_stage()
        for path in paths:
            prim = stage.GetPrimAtPath(path)
            if prim.IsValid():
                prim.SetInstanceable(True)


    def _get_bed_row_positions(self, bed, center_y):
        available_length = self._cfg.length - (self._cfg.edge_width * 2)
        num_plants = int(available_length // bed.plant_distance)

        y_coords = np.linspace(-available_length/2, available_length/2, num_plants + 1)
        
        x_coords = np.linspace(center_y - ((bed.rows - 1) * bed.row_spacing) / 2, 
                            center_y + ((bed.rows - 1) * bed.row_spacing) / 2, bed.rows)

        xv, yv = np.meshgrid(x_coords, y_coords)
        formula = getattr(bed, 'math_formula', None)

        if formula:
            offsets = get_custom_y_offsets(formula, yv)
            xv += offsets

        positions = np.stack([xv.ravel(), yv.ravel()], axis=-1)
        
        jitter = (np.random.rand(*positions.shape) * 2 - 1) * bed.plant_jitter
        positions += jitter
        
        mask = np.random.rand(len(positions)) > bed.skip_chance
        return positions[mask]
        

    def _get_random_material(self, prim_path: str):
        if not prim_path or prim_path.strip() == "":
            return None
        parent = self.stage.GetPrimAtPath(prim_path)
        if not parent or not parent.IsValid():
            carb.log_warn(f"Material parent prim not found at: {prim_path}")
            return None


        materials = []
        for child in parent.GetChildren():
            mat = UsdShade.Material(child)
            if mat and mat.GetPrim().IsValid():
                materials.append(mat)

        if not materials:
            carb.log_warn(f"No materials found under: {prim_path}")
            return None

        return random.choice(materials)

    

    
    def _get_weeds_positions(self, density: int):
        x = None
        y = None

        if density:

                available_width = self._cfg.width - (self._cfg.edge_width * 2)
                available_length = self._cfg.length - (self._cfg.edge_width * 2)
                n_points = int(available_width  * available_length * density)
                x = np.random.uniform(0, available_width, n_points) - available_width/2
                y = np.random.uniform(0, available_length, n_points) - available_length/2
        else:
            return None

        return np.column_stack((x, y))

    def create_ground_plane(self, width=1.0, length=1.0, z=0.0, plane_path="/World/Field/Ground"):
        if plane_path is None:
            plane_path = f"{self.field_prim_path}/Ground"

        prim = self.stage.GetPrimAtPath(plane_path)
        if prim and prim.IsValid():
            self.stage.RemovePrim(plane_path)

        noise_array = self._terrain.generate(width, length, z)


        terrain_prim = self.create_terrain_from_noise(
            noise_array=noise_array, 
            width=width, 
            length=length,
            plane_path=plane_path
        )
        

        mesh = UsdGeom.Mesh(terrain_prim)
        texCoords = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.varying
        )
    
        rows, cols = noise_array.shape
        u = np.linspace(0, 1, cols)
        v = np.linspace(0, 1, rows)
        uu, vv = np.meshgrid(u, v)
        uv_coords = np.stack([uu.ravel(), vv.ravel()], axis=-1)
        

        texCoords.Set(Vt.Vec2fArray.FromNumpy(uv_coords.astype(np.float32)))

        return terrain_prim
    
    def _bind_random_material_to_ground(self, mat_root_path, ground_prim):

        material = self._get_random_material(mat_root_path)
        if not material:
            return

        prop_path = f"{material.GetPath()}/Shader.inputs:texture_translate"

        if self.stage.GetAttributeAtPath(prop_path):
            omni.kit.commands.execute(
                "ChangePropertyCommand",
                prop_path=prop_path,
                value=(random.random(), random.random()),
                prev=(0, 0)
            )
        else:
            carb.log_warn(f"Material {material.GetPath()} missing texture_translate input. Skipping offset randomization.")
        UsdShade.MaterialBindingAPI(ground_prim).Bind(material)


    def get_usd_safe_name(self, name):
        safe_name = re.sub(r'[^\w]', '_', name)
        if safe_name[0].isdigit():
            safe_name = f"bed_{safe_name}"
        return safe_name

    def show_random_light(self, scope_path):
        light = self.show_one_random_child(scope_path)
        if not light:
            return


        x_deg = random.uniform(*self._cfg.light_x_range)
        y_deg = random.uniform(*self._cfg.light_y_range)
        z_deg = random.uniform(*self._cfg.light_z_range)


        rX = Gf.Rotation(Gf.Vec3d(1, 0, 0), float(x_deg))
        rY = Gf.Rotation(Gf.Vec3d(0, 1, 0), float(y_deg))
        rZ = Gf.Rotation(Gf.Vec3d(0, 0, 1), float(z_deg))
        
        rot = rZ * rY * rX
        orient_attr = light.GetAttribute("xformOp:orient")
        
        if orient_attr:
            qd = rot.GetQuat()
            if str(orient_attr.GetTypeName()) == "quatf":
                orient_attr.Set(Gf.Quatf(float(qd.GetReal()), Gf.Vec3f(*qd.GetImaginary())))
            else:
                orient_attr.Set(qd)
                
    def show_one_random_child(self, parent_path="/World/Lighting"):
        if not parent_path or parent_path.strip() == "":
            return None
        parent = self.stage.GetPrimAtPath(parent_path)
        if not parent or not parent.IsValid():
            carb.log_warn(f"Parent prim not found: {parent_path}")
            return None

        imageable_children = []
        for child in parent.GetChildren():
            img = UsdGeom.Imageable(child)
            if img and img.GetPrim().IsValid():
                imageable_children.append(child)

        if not imageable_children:
            return None

        for ch in imageable_children:
            UsdGeom.Imageable(ch).CreateVisibilityAttr().Set("invisible")

        chosen = random.choice(imageable_children)
        UsdGeom.Imageable(chosen).CreateVisibilityAttr().Set("inherited")

        return chosen