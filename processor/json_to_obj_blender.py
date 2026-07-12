 import bpy
import sys
import os
import json

# دریافت آرگومان‌ها
input_json = sys.argv[-2]
output_obj = sys.argv[-1]

print(f"Blender Converter: {input_json} → {output_obj}")

# پاک کردن صحنه
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

# بارگذاری JSON
with open(input_json, 'r', encoding='utf-8') as f:
    data = json.load(f)

# ایجاد آبجکت
bpy.ops.object.add(type='MESH', enter_editmode=False, align='WORLD', location=(0, 0, 0))
obj = bpy.context.active_object
mesh = obj.data

vertices = []
faces = []
uvs = []

elements = data.get('elements', [])

for el in elements:
    from_pos = el.get('from', [0,0,0])
    to_pos = el.get('to', [1,1,1])
    
    # 8 رأس مکعب
    v_base = len(vertices)
    verts = [
        (from_pos[0], from_pos[1], from_pos[2]),
        (to_pos[0],   from_pos[1], from_pos[2]),
        (to_pos[0],   to_pos[1],   from_pos[2]),
        (from_pos[0], to_pos[1],   from_pos[2]),
        (from_pos[0], from_pos[1], to_pos[2]),
        (to_pos[0],   from_pos[1], to_pos[2]),
        (to_pos[0],   to_pos[1],   to_pos[2]),
        (from_pos[0], to_pos[1],   to_pos[2]),
    ]
    vertices.extend(verts)

    # Faces (6 وجه)
    face_indices = [
        (v_base+0, v_base+1, v_base+2, v_base+3),  # north
        (v_base+1, v_base+5, v_base+6, v_base+2),  # east
        (v_base+5, v_base+4, v_base+7, v_base+6),  # south
        (v_base+4, v_base+0, v_base+3, v_base+7),  # west
        (v_base+3, v_base+2, v_base+6, v_base+7),  # up
        (v_base+0, v_base+4, v_base+5, v_base+1),  # down
    ]
    faces.extend(face_indices)

# ایجاد مش
mesh.from_pydata(vertices, [], faces)
mesh.update()

# ساده‌ترین UV (برای شروع)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project()
bpy.ops.object.mode_set(mode='OBJECT')

# اکسپورت OBJ
bpy.ops.export_scene.obj(
    filepath=output_obj,
    use_selection=False,
    use_materials=True,
    use_uvs=True,
    use_normals=True,
    keep_vertex_order=True,
    use_triangles=False
)

print(f"✅ Blender conversion completed: {output_obj}")