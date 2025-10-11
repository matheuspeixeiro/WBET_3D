# monitor_core.py
# Core monitor / gaze utilities extracted from MonitorTracking.py
# (keeps original function names so existing calls won't break)
#
# Based on user's MonitorTracking.py (extracted functions and helpers).
# See original for interactive loop / camera capture / main program.
# Citation: MonitorTracking.py from the user's project. :contentReference[oaicite:1]{index=1}

import cv2
import numpy as np
import math
import time
import threading
from collections import deque
from scipy.spatial.transform import Rotation as Rscipy
import pyautogui
import mediapipe as mp
import keyboard  # used only in update_orbit_from_keys (the caller may choose not to call it)

# Monitor / screen dimensions (used by convert_gaze_to_screen_coordinates)
MONITOR_WIDTH, MONITOR_HEIGHT = pyautogui.size()
CENTER_X = MONITOR_WIDTH // 2
CENTER_Y = MONITOR_HEIGHT // 2

# --- Orbit camera state for debug view (kept as in original) ---
orbit_yaw   = -151.0
orbit_pitch = 0.0
orbit_radius = 1500.0
orbit_fov_deg = 50.0

# --- Debug-world freeze (used after calibration) ---
debug_world_frozen = False
orbit_pivot_frozen = None

# --- 3D monitor plane state (globals — updated by calibration code elsewhere) ---
monitor_corners = None   # list of 4 world points (p0..p3)
monitor_center_w = None
monitor_normal_w = None
units_per_cm = None

# gaze markers stored on monitor plane (a,b in plane coords)
gaze_markers = []

# Mouse target and lock (preserved names)
mouse_target = [CENTER_X, CENTER_Y]
mouse_lock = threading.Lock()
mouse_control_enabled = False

# Calibration offsets for screen mapping (updated during "s" calibration)
calibration_offset_yaw = 0
calibration_offset_pitch = 0

# Filtering buffers
filter_length = 10
combined_gaze_directions = deque(maxlen=filter_length)

# Reference matrices to avoid eigenvector flips
R_ref_nose = [None]
R_ref_forehead = [None]
calibration_nose_scale = None

# MediaPipe face mesh (left here for compatibility; EyeTracker may re-create)
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# Camera frame size placeholders (set by caller if needed)
w = 640
h = 480

# Nose landmark indices used in compute_and_draw_coordinate_box
nose_indices = [4, 45, 275, 220, 440, 1, 5, 51, 281, 44, 274, 241,
                461, 125, 354, 218, 438, 195, 167, 393, 165, 391,
                3, 248]

# File for writing screen position (kept name)
screen_position_file = "screen_position.txt"


def write_screen_position(x, y):
    """Write screen position to file, overwriting the same line"""
    try:
        with open(screen_position_file, 'w') as f:
            f.write(f"{x},{y}\n")
    except Exception:
        # fail silently here; caller may log
        pass


def _rot_x(a):
    ca, sa = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0],
                     [0, ca, -sa],
                     [0, sa,  ca]], dtype=float)


def _rot_y(a):
    ca, sa = math.cos(a), math.sin(a)
    return np.array([[ ca, 0, sa],
                     [  0, 1,  0],
                     [-sa, 0, ca]], dtype=float)


def _normalize(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def _focal_px(width, fov_deg):
    """Horizontal pinhole focal length (pixels) for given horizontal fov"""
    return 0.5 * width / math.tan(math.radians(fov_deg) * 0.5)


def compute_scale(points_3d):
    """
    Robust measure of size of a point-set: average pairwise distance.
    (Used to scale calibration offsets when head distance changes.)
    """
    n = len(points_3d)
    total = 0.0
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            dist = np.linalg.norm(points_3d[i] - points_3d[j])
            total += dist
            count += 1
    return total / count if count > 0 else 1.0


def draw_wireframe_cube(frame, center, R, size=80):
    """Draw a wireframe cube given center (3d coords projected as pixel positions already)."""
    # This function expects the 'center' to be in 2D pixel coordinates in the original code usage.
    # Keep the original implementation (projects corners to 2D using the passed center values).
    right = R[:, 0]
    up = -R[:, 1]
    forward = -R[:, 2]

    hw, hh, hd = size * 1, size * 1, size * 1

    def corner(x_sign, y_sign, z_sign):
        return (center +
                x_sign * hw * right +
                y_sign * hh * up +
                z_sign * hd * forward)

    corners = [corner(x, y, z) for x in [-1, 1] for y in [1, -1] for z in [-1, 1]]
    projected = [(int(pt[0]), int(pt[1])) for pt in corners]

    edges = [
        (0, 1), (1, 3), (3, 2), (2, 0),
        (4, 5), (5, 7), (7, 6), (6, 4),
        (0, 4), (1, 5), (2, 6), (3, 7)
    ]
    for i, j in edges:
        cv2.line(frame, projected[i], projected[j], (255, 128, 0), 2)


def compute_and_draw_coordinate_box(frame, face_landmarks, indices, ref_matrix_container, color=(0, 255, 0), size=80):
    """
    From a list of face_landmarks (mediapipe style), extract selected indices,
    compute a PCA orientation and draw a small wireframe cube + axes on the frame.

    Returns (center3d, R_final, points_3d)
    """
    global w, h
    points_3d = np.array([
        [face_landmarks[i].x * w, face_landmarks[i].y * h, face_landmarks[i].z * w]
        for i in indices
    ])

    center = np.mean(points_3d, axis=0)

    # Draw raw 2D points
    for i in indices:
        x, y = int(face_landmarks[i].x * w), int(face_landmarks[i].y * h)
        cv2.circle(frame, (x, y), 3, color, -1)

    # PCA orientation
    centered = points_3d - center
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    eigvecs = eigvecs[:, np.argsort(-eigvals)]

    if np.linalg.det(eigvecs) < 0:
        eigvecs[:, 2] *= -1

    r = Rscipy.from_matrix(eigvecs)
    roll, pitch, yaw = r.as_euler('zyx', degrees=False)
    # keep as-is from original (some multipliers were 1)
    R_final = Rscipy.from_euler('zyx', [roll, pitch, yaw]).as_matrix()

    # Stabilize eigenvector flipping using provided reference container
    if ref_matrix_container[0] is None:
        ref_matrix_container[0] = R_final.copy()
    else:
        R_ref = ref_matrix_container[0]
        for i in range(3):
            if np.dot(R_final[:, i], R_ref[:, i]) < 0:
                R_final[:, i] *= -1

    draw_wireframe_cube(frame, center, R_final, size)

    axis_length = size * 1.2
    axis_dirs = [R_final[:, 0], -R_final[:, 1], -R_final[:, 2]]
    axis_colors = [(0, 255, 0), (0, 0, 255), (255, 0, 0)]
    for i in range(3):
        end_pt = center + axis_dirs[i] * axis_length
        cv2.line(frame, (int(center[0]), int(center[1])),
                 (int(end_pt[0]), int(end_pt[1])), axis_colors[i], 2)

    return center, R_final, points_3d


def create_monitor_plane(head_center, R_final, face_landmarks, w_local, h_local,
                         forward_hint=None, gaze_origin=None, gaze_dir=None):
    """
    Build a 60cm x 40cm monitor plane placed ~50cm from the head in world units.
    Returns (monitor_corners_list, center_w, normal_w, units_per_cm).
    """
    # Estimate scale from chin <-> forehead
    try:
        lm_chin = face_landmarks[152]
        lm_fore = face_landmarks[10]
        chin_w = np.array([lm_chin.x * w_local,  lm_chin.y * h_local,  lm_chin.z * w_local], dtype=float)
        fore_w = np.array([lm_fore.x * w_local,  lm_fore.y * h_local,  lm_fore.z * w_local], dtype=float)
        face_h_units = np.linalg.norm(fore_w - chin_w)
        upc = face_h_units / 15.0
    except Exception:
        upc = 5.0

    mon_w_cm, mon_h_cm = 60.0, 40.0
    half_w = (mon_w_cm * 0.5) * upc
    half_h = (mon_h_cm * 0.5) * upc

    head_forward = -R_final[:, 2]
    if forward_hint is not None:
        head_forward = forward_hint / (np.linalg.norm(forward_hint) + 1e-12)

    # prefer gaze intersection if provided
    if gaze_origin is not None and gaze_dir is not None:
        gaze_dir = gaze_dir / (np.linalg.norm(gaze_dir) + 1e-12)
        plane_point = head_center + head_forward * (50.0 * upc)
        plane_normal = head_forward

        denom = np.dot(plane_normal, gaze_dir)
        if abs(denom) > 1e-6:
            t = np.dot(plane_normal, plane_point - gaze_origin) / denom
            center_w = gaze_origin + t * gaze_dir
        else:
            center_w = head_center + head_forward * (50.0 * upc)
    else:
        center_w = head_center + head_forward * (50.0 * upc)

    world_up = np.array([0, -1, 0], dtype=float)
    head_right = np.cross(world_up, head_forward)
    head_right /= (np.linalg.norm(head_right) + 1e-12)
    head_up = np.cross(head_forward, head_right)
    head_up /= (np.linalg.norm(head_up) + 1e-12)

    p0 = center_w - head_right * half_w - head_up * half_h
    p1 = center_w + head_right * half_w - head_up * half_h
    p2 = center_w + head_right * half_w + head_up * half_h
    p3 = center_w - head_right * half_w + head_up * half_h

    normal_w = head_forward / (np.linalg.norm(head_forward) + 1e-9)
    return [p0, p1, p2, p3], center_w, normal_w, upc


def update_orbit_from_keys():
    """Keyboard orbit controls (prints on change)."""
    global orbit_yaw, orbit_pitch, orbit_radius, orbit_fov_deg
    yaw_step = math.radians(1.5)
    pitch_step = math.radians(1.5)
    zoom_step = 12.0
    changed = False

    if keyboard.is_pressed('j'):
        orbit_yaw -= yaw_step; changed = True
    if keyboard.is_pressed('l'):
        orbit_yaw += yaw_step; changed = True
    if keyboard.is_pressed('i'):
        orbit_pitch += pitch_step; changed = True
    if keyboard.is_pressed('k'):
        orbit_pitch -= pitch_step; changed = True
    if keyboard.is_pressed('['):
        orbit_radius += zoom_step; changed = True
    if keyboard.is_pressed(']'):
        orbit_radius = max(80.0, orbit_radius - zoom_step); changed = True
    if keyboard.is_pressed('r'):
        orbit_yaw = 0.0; orbit_pitch = 0.0; orbit_radius = 600.0; changed = True

    orbit_pitch = max(math.radians(-89), min(math.radians(89), orbit_pitch))
    orbit_radius = max(80.0, orbit_radius)

    if changed:
        print(f"[Orbit Debug] yaw={math.degrees(orbit_yaw):.2f}°, "
              f"pitch={math.degrees(orbit_pitch):.2f}°, "
              f"radius={orbit_radius:.2f}, "
              f"fov={orbit_fov_deg:.1f}°")


def render_debug_view_orbit(
    h_local, w_local,
    head_center3d=None,
    sphere_world_l=None, scaled_radius_l=None,
    sphere_world_r=None, scaled_radius_r=None,
    iris3d_l=None, iris3d_r=None,
    left_locked=False, right_locked=False,
    landmarks3d=None,
    combined_dir=None,
    gaze_len=430,
    monitor_corners=None,
    monitor_center=None,
    monitor_normal=None,
    gaze_markers_arg=None,
):
    """
    Construct and return a debug image (numpy array, h_local x w_local x 3).
    This is a faithful port of the original debug renderer.
    """
    global debug_world_frozen, orbit_pivot_frozen, orbit_yaw, orbit_pitch, orbit_radius, orbit_fov_deg, units_per_cm
    if head_center3d is None:
        return None

    debug = np.zeros((h_local, w_local, 3), dtype=np.uint8)
    head_w = np.asarray(head_center3d, dtype=float)

    if debug_world_frozen and orbit_pivot_frozen is not None:
        pivot_w = np.asarray(orbit_pivot_frozen, dtype=float)
    else:
        if monitor_center is not None:
            pivot_w = (head_w + np.asarray(monitor_center, dtype=float)) * 0.5
        else:
            pivot_w = head_w

    f_px = _focal_px(w_local, orbit_fov_deg)
    cam_offset = _rot_y(orbit_yaw) @ (_rot_x(orbit_pitch) @ np.array([0.0, 0.0, orbit_radius]))
    cam_pos = pivot_w + cam_offset

    up_world = np.array([0.0, -1.0, 0.0])
    fwd = _normalize(pivot_w - cam_pos)
    right = _normalize(np.cross(fwd, up_world))
    up = _normalize(np.cross(right, fwd))
    V = np.stack([right, up, fwd], axis=0)

    def project_point(P):
        Pw = np.asarray(P, dtype=float)
        Pc = V @ (Pw - cam_pos)
        if Pc[2] <= 1e-3:
            return None
        x = f_px * (Pc[0] / Pc[2]) + w_local * 0.5
        y = -f_px * (Pc[1] / Pc[2]) + h_local * 0.5
        if not (np.isfinite(x) and np.isfinite(y)):
            return None
        return (int(x), int(y)), Pc[2]

    def draw_poly_3d(pts, color=(0, 200, 255), thickness=2):
        projs = [project_point(p) for p in pts]
        if any(p is None for p in projs):
            return
        p2 = [p[0] for p in projs]
        for a, b in zip(p2, p2[1:] + [p2[0]]):
            cv2.line(debug, a, b, color, thickness)

    def draw_cross_3d(P, size=12, color=(255, 0, 255), thickness=2):
        res = project_point(P)
        if res is None: return
        (x, y), _ = res
        cv2.line(debug, (x - size, y), (x + size, y), color, thickness)
        cv2.line(debug, (x, y - size), (x, y + size), color, thickness)

    def draw_arrow_3d(P0, P1, color=(0, 200, 255), thickness=3):
        a = project_point(P0); b = project_point(P1)
        if a is None or b is None: return
        p0, p1 = a[0], b[0]
        cv2.line(debug, p0, p1, color, thickness)
        v = np.array([p1[0]-p0[0], p1[1]-p0[1]], dtype=float)
        n = np.linalg.norm(v)
        if n > 1e-3:
            v /= n
            l = np.array([-v[1], v[0]])
            ah = 10
            a1 = (int(p1[0] - v[0]*ah + l[0]*ah*0.6), int(p1[1] - v[1]*ah + l[1]*ah*0.6))
            a2 = (int(p1[0] - v[0]*ah - l[0]*ah*0.6), int(p1[1] - v[1]*ah - l[1]*ah*0.6))
            cv2.line(debug, p1, a1, color, thickness)
            cv2.line(debug, p1, a2, color, thickness)

    # Render landmarks (if any)
    if landmarks3d is not None:
        for P in landmarks3d:
            res = project_point(P)
            if res is not None:
                cv2.circle(debug, res[0], 0, (200, 200, 200), -1)

    # Head center cross
    draw_cross_3d(head_w, size=12, color=(255, 0, 255), thickness=2)
    hc2d = project_point(head_w)
    if hc2d is not None:
        cv2.putText(debug, "Head Center", (hc2d[0][0] + 12, hc2d[0][1] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1, cv2.LINE_AA)

    # Pivot visual
    draw_cross_3d(pivot_w, size=8, color=(180, 120, 255), thickness=2)
    if monitor_center is not None:
        mc2d = project_point(monitor_center)
        pv2d = project_point(pivot_w)
        if mc2d is not None and pv2d is not None and hc2d is not None:
            cv2.line(debug, pv2d[0], hc2d[0], (160, 100, 255), 1)
            cv2.line(debug, pv2d[0], mc2d[0], (160, 100, 255), 1)

    # Eyes + per-eye gaze
    left_dir = None
    right_dir = None

    if left_locked and sphere_world_l is not None:
        res = project_point(sphere_world_l)
        if res is not None:
            (cx, cy), z = res
            r_px = max(2, int((scaled_radius_l if scaled_radius_l else 6) * f_px / max(z, 1e-3)))
            cv2.circle(debug, (cx, cy), r_px, (255, 255, 25), 1)
            if iris3d_l is not None:
                left_dir = np.asarray(iris3d_l) - np.asarray(sphere_world_l)
                p1 = project_point(np.asarray(sphere_world_l) + _normalize(left_dir) * gaze_len)
                if p1 is not None:
                    cv2.line(debug, (cx, cy), p1[0], (155, 155, 25), 1)
    elif iris3d_l is not None:
        res = project_point(iris3d_l)
        if res is not None:
            cv2.circle(debug, res[0], 2, (255, 255, 25), 1)

    if right_locked and sphere_world_r is not None:
        res = project_point(sphere_world_r)
        if res is not None:
            (cx, cy), z = res
            r_px = max(2, int((scaled_radius_r if scaled_radius_r else 6) * f_px / max(z, 1e-3)))
            cv2.circle(debug, (cx, cy), r_px, (25, 255, 255), 1)
            if iris3d_r is not None:
                right_dir = np.asarray(iris3d_r) - np.asarray(sphere_world_r)
                p1 = project_point(np.asarray(sphere_world_r) + _normalize(right_dir) * gaze_len)
                if p1 is not None:
                    cv2.line(debug, (cx, cy), p1[0], (25, 155, 155), 1)
    elif iris3d_r is not None:
        res = project_point(iris3d_r)
        if res is not None:
            cv2.circle(debug, res[0], 2, (25, 255, 255), 1)

    # Combined gaze ray & monitor intersection visuals
    if left_locked and right_locked and sphere_world_l is not None and sphere_world_r is not None:
        origin_mid = (np.asarray(sphere_world_l) + np.asarray(sphere_world_r)) / 2.0
        if combined_dir is None and (left_dir is not None or right_dir is not None):
            parts = []
            if left_dir is not None: parts.append(_normalize(left_dir))
            if right_dir is not None: parts.append(_normalize(right_dir))
            if parts:
                combined_dir = _normalize(np.mean(parts, axis=0))
        if combined_dir is not None:
            p0 = project_point(origin_mid)
            p1 = project_point(origin_mid + _normalize(combined_dir) * (gaze_len * 1.2))
            if p0 is not None and p1 is not None:
                cv2.line(debug, p0[0], p1[0], (155, 200, 10), 2)

    # Monitor plane drawing
    if monitor_corners is not None:
        draw_poly_3d(monitor_corners)
        draw_poly_3d([monitor_corners[0], monitor_corners[2]], color=(0, 150, 210), thickness=1)
        draw_poly_3d([monitor_corners[1], monitor_corners[3]], color=(0, 150, 210), thickness=1)
        if monitor_center is not None:
            draw_cross_3d(monitor_center, size=8, color=(0, 200, 255), thickness=2)
            if monitor_normal is not None:
                tip = np.asarray(monitor_center) + np.asarray(monitor_normal) * (20.0 * (units_per_cm or 1.0))
                draw_arrow_3d(monitor_center, tip, color=(0, 220, 255), thickness=2)

    # Gaze markers on monitor plane
    if gaze_markers_arg and monitor_corners is not None:
        p0, p1, p2, p3 = [np.asarray(p, dtype=float) for p in monitor_corners]
        u = p1 - p0
        v = p3 - p0
        width_world = float(np.linalg.norm(u))
        if width_world > 1e-9:
            u_hat = u / width_world
            r_world = 0.01 * width_world
            for (a, b) in gaze_markers_arg:
                Pm = p0 + a * u + b * v
                projP = project_point(Pm)
                projR = project_point(Pm + u_hat * r_world)
                if projP is not None and projR is not None:
                    center_px = projP[0]
                    r_px = int(max(1, np.linalg.norm(np.array(projR[0]) - np.array(center_px))))
                    cv2.circle(debug, center_px, r_px, (0, 255, 0), 1, lineType=cv2.LINE_AA)

    # Gaze hit circle on monitor if possible
    if (monitor_corners is not None and monitor_center is not None and monitor_normal is not None
            and combined_dir is not None and sphere_world_l is not None and sphere_world_r is not None):
        O = (np.asarray(sphere_world_l, dtype=float) + np.asarray(sphere_world_r, dtype=float)) * 0.5
        D = _normalize(np.asarray(combined_dir, dtype=float))
        C = np.asarray(monitor_center, dtype=float)
        N = _normalize(np.asarray(monitor_normal, dtype=float))
        denom = float(np.dot(N, D))
        if abs(denom) > 1e-6:
            t = float(np.dot(N, (C - O)) / denom)
            if t > 0.0:
                P = O + t * D
                p0, p1, p2, p3 = [np.asarray(p, dtype=float) for p in monitor_corners]
                u = p1 - p0
                v = p3 - p0
                wv = P - p0
                u_len2 = float(np.dot(u, u)); v_len2 = float(np.dot(v, v))
                if u_len2 > 1e-9 and v_len2 > 1e-9:
                    a = float(np.dot(wv, u) / u_len2)
                    b = float(np.dot(wv, v) / v_len2)
                    if 0.0 <= a <= 1.0 and 0.0 <= b <= 1.0:
                        projP = project_point(P)
                        if projP is not None:
                            center_px = projP[0]
                            width_world = math.sqrt(u_len2)
                            r_world = 0.05 * width_world
                            u_hat = u / max(width_world, 1e-9)
                            projR = project_point(P + u_hat * r_world)
                            if projR is not None:
                                r_px = int(max(1, np.linalg.norm(np.array(projR[0]) - np.array(center_px))))
                                cv2.circle(debug, center_px, r_px, (0, 255, 255), 2, lineType=cv2.LINE_AA)

    # Help text bottom-left (as in original)
    help_text = [
        "C = calibrate screen center",
        "J = yaw left",
        "L = yaw right",
        "I = pitch up",
        "K = pitch down",
        "[ = zoom out",
        "] = zoom in",
        "R = reset view",
        "X = add marker",
        "q = quit",
        "F7 = toggle mouse control"
    ]
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.5
    thickness = 1
    line_height = 18
    y0 = h_local - (len(help_text) * line_height) - 10
    x0 = 10
    for i, text in enumerate(help_text):
        y = y0 + i * line_height
        cv2.putText(debug, text, (x0, y), font, font_scale, (200, 200, 200), thickness, cv2.LINE_AA)

    return debug


def draw_gaze(frame, eye_center, iris_center, eye_radius, color, gaze_length):
    """
    Draw a stylized gaze ray on 'frame' given eye center and iris 3D/pixel coords.
    This function was copied verbatim to preserve original visuals.
    """
    gaze_direction = iris_center - eye_center
    gaze_direction /= (np.linalg.norm(gaze_direction) + 1e-12)
    gaze_endpoint = eye_center + gaze_direction * gaze_length

    cv2.line(frame, tuple(int(v) for v in eye_center[:2]), tuple(int(v) for v in gaze_endpoint[:2]), color, 2)

    iris_offset = eye_center + gaze_direction * (1.2 * eye_radius)

    cv2.line(frame, (int(eye_center[0]), int(eye_center[1])),
             (int(iris_offset[0]), int(iris_offset[1])), color, 1)

    up_dir = np.array([0, -1, 0])
    right_dir = np.cross(gaze_direction, up_dir)
    if np.linalg.norm(right_dir) < 1e-6:
        right_dir = np.array([1, 0, 0])
    up_dir = np.cross(right_dir, gaze_direction)
    up_dir /= (np.linalg.norm(up_dir) + 1e-12)
    right_dir /= (np.linalg.norm(right_dir) + 1e-12)
    # ellipse axes and angle (not used for drawing ellipse here, but kept for fidelity)
    ellipse_axes = (
        int((eye_radius / 3) * np.linalg.norm(right_dir[:2])),
        int((eye_radius / 3) * np.linalg.norm(up_dir[:2]))
    )
    angle = math.degrees(math.atan2(gaze_direction[1], gaze_direction[0]))

    cv2.line(frame, (int(iris_offset[0]), int(iris_offset[1])),
             (int(gaze_endpoint[0]), int(gaze_endpoint[1])), color, 1)


def convert_gaze_to_screen_coordinates(combined_gaze_direction, calibration_offset_yaw, calibration_offset_pitch):
    """
    Convert a 3D gaze direction to 2D screen coordinates using the same mapping
    logic as the original script. Returns (screen_x, screen_y, raw_yaw_deg, raw_pitch_deg).
    """
    reference_forward = np.array([0, 0, -1])
    avg_direction = combined_gaze_direction / (np.linalg.norm(combined_gaze_direction) + 1e-12)

    # Yaw: project into XZ plane
    xz_proj = np.array([avg_direction[0], 0, avg_direction[2]])
    xz_proj /= (np.linalg.norm(xz_proj) + 1e-12)
    yaw_rad = math.acos(np.clip(np.dot(reference_forward, xz_proj), -1.0, 1.0))
    if avg_direction[0] < 0:
        yaw_rad = -yaw_rad

    # Pitch: project into YZ plane
    yz_proj = np.array([0, avg_direction[1], avg_direction[2]])
    yz_proj /= (np.linalg.norm(yz_proj) + 1e-12)
    pitch_rad = math.acos(np.clip(np.dot(reference_forward, yz_proj), -1.0, 1.0))
    if avg_direction[1] > 0:
        pitch_rad = -pitch_rad

    yaw_deg = np.degrees(yaw_rad)
    pitch_deg = np.degrees(pitch_rad)

    if yaw_deg < 0:
        yaw_deg = -(yaw_deg)
    elif yaw_deg > 0:
        yaw_deg = -yaw_deg

    raw_yaw_deg = yaw_deg
    raw_pitch_deg = pitch_deg

    yawDegrees = 5 * 3
    pitchDegrees = 2.0 * 2.5

    yaw_deg += calibration_offset_yaw
    pitch_deg += calibration_offset_pitch

    screen_x = int(((yaw_deg + yawDegrees) / (2 * yawDegrees)) * MONITOR_WIDTH)
    screen_y = int(((pitchDegrees - pitch_deg) / (2 * pitchDegrees)) * MONITOR_HEIGHT)

    screen_x = max(10, min(screen_x, MONITOR_WIDTH - 10))
    screen_y = max(10, min(screen_y, MONITOR_HEIGHT - 10))

    return screen_x, screen_y, raw_yaw_deg, raw_pitch_deg


def mouse_mover():
    """
    Thread worker to move the OS mouse to mouse_target when mouse_control_enabled is True.
    (Original script started this thread at module import; here we only define it.)
    """
    global mouse_control_enabled
    while True:
        if mouse_control_enabled:
            with mouse_lock:
                x, y = mouse_target
            try:
                pyautogui.moveTo(x, y)
            except Exception:
                pass
        time.sleep(0.01)
