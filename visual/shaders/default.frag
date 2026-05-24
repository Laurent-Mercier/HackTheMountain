#version 450

// Fragment uniform slot 0 → SPIR-V set=3, binding=0
layout(set = 3, binding = 0) uniform Uniforms {
    float time;
    float aspect;
    vec2  resolution;
    vec2  mouse;
};

// Fragment uniform slot 1 → SPIR-V set=3, binding=1
// Mirrors AudioUniforms in main.cpp (std140).
layout(set = 3, binding = 1) uniform AudioUniforms {
    float volume_db;     // [-80, 0]  dBFS
    float bass;          // [0, 1]    20–250 Hz relative energy
    float mid;           // [0, 1]    250–4k Hz
    float treble;        // [0, 1]    4k–16k Hz
    float bpm;           // [-1, 320] beats/min; -1 = unknown
    float smoothness;    // [0, 1]    0 = percussive, 1 = sine-like
    float centroid_hz;   // [0, 22050] raw spectral centroid
    float centroid_n;    // [0, 1]    log-mapped perceptual brightness
    float note;          // [0, 127]  MIDI note number; pitch class = mod(note, 12.0), octave = floor(note/12.0)-1
    float note_strength; // [0, 1]    chroma energy of dominant note
    float _pad[2];
    vec4  chroma[3];     // 12 pitch-class energies packed as 3×vec4
                         // chroma[0].xyzw = C C# D D#, [1] = E F F# G, [2] = G# A A# B
} audio;

layout(location = 0) in  vec2 v_uv;
layout(location = 0) out vec4 out_color;

// ── Generic utilities (reusable with any dist function) ───────────────────────

vec3 cosine_palette(float t, vec3 a, vec3 b, vec3 c, vec3 d) {
    return a + b * cos(6.28318 * (c * t + d));
}

// Diffuse + tinted specular Phong. shadow in [0,1] scales non-ambient terms only.
vec3 phong(vec3 col, vec3 normal, vec3 light, vec3 view, float ambient, float shininess, float shadow) {
    float diff = max(dot(normal, light), 0.0) * shadow;
    vec3  refl = reflect(-light, normal);
    float spec = pow(max(dot(refl, view), 0.0), shininess) * shadow;
    return col * (ambient + (1.0 - ambient) * diff) + col * 0.6 * spec;
}

// mouse is [0,1] with Y=0 at top; map to a world-space light direction.
vec3 mouse_light() {
    return normalize(vec3(1.0 - mouse * 2.0, -1.0));
}

// Fast scalar hash for a 3D integer cell index.
float cell_hash(vec3 cell_id) {
    return fract(sin(dot(cell_id, vec3(127.1, 311.7, 74.4))) * 43758.5453);
}

// ── Ray marching constants ────────────────────────────────────────────────────

const float MAXIMUM_TRACE_DISTANCE = 1000.0;
const int   NUMBER_OF_STEPS        = 128;
const float MINIMUM_HIT_DISTANCE   = 0.001;
const float SHADOW_MIN             = 0.4;

// ── Fractal distance estimator (Mandelbulb, tiled) ───────────────────────────

const float CELL_SIZE = 2.2;
const int   N_ITER    = 16;
const int   EXP       = 8;

float dist(vec3 p, out vec4 trap) {
    vec3 displaced = p;
    displaced.x += sin(time * 0.5 + p.x * 3.0) * 0.15;
    displaced.y += cos(-time * 0.7 + p.y * 3.0) * 0.15;
    displaced.z += sin(time * 0.6 + p.z * 3.0) * 0.15;
    p.xyz = mod(displaced.xzy, CELL_SIZE) - 0.5 * CELL_SIZE;
    vec3  w  = p;
    float r  = 0.0;
    float dr = 1.0;

    trap = vec4(abs(w), r);

    for (int i = 0; i < N_ITER; ++i) {
        r = length(w);
        if (r > 2.0) break;

        float phi   = atan(w.y, w.x);
        float theta = acos(w.z / r);

        dr = pow(r, EXP - 1.0) * EXP * dr + 1.0;

        float zr = pow(r, EXP);
        w = p + zr * vec3(sin(float(EXP) * theta) * cos(float(EXP) * phi),
                          sin(float(EXP) * theta) * sin(float(EXP) * phi),
                          cos(float(EXP) * theta));
        trap = min(trap, vec4(abs(w), r));
    }

    trap = vec4(r, trap.yzw);
    return (0.5 * log(r) * r / dr);
}

// ── Shape-agnostic ray marching helpers ──────────────────────────────────────

float ray_march(vec3 ro, vec3 rd, out vec4 trap) {
    float traveled = 0.0;
    for (int i = 0; i < NUMBER_OF_STEPS; ++i) {
        float d = dist(ro + rd * traveled, trap);
        if (d < MINIMUM_HIT_DISTANCE) return traveled;
        if (traveled > MAXIMUM_TRACE_DISTANCE) break;
        traveled += d;
    }
    return MAXIMUM_TRACE_DISTANCE;
}

vec3 calc_normal(vec3 p) {
    const float e = 0.004;
    vec4 _t;
    return normalize(vec3(
        dist(p + vec3(e, 0, 0), _t) - dist(p - vec3(e, 0, 0), _t),
        dist(p + vec3(0, e, 0), _t) - dist(p - vec3(0, e, 0), _t),
        dist(p + vec3(0, 0, e), _t) - dist(p - vec3(0, 0, e), _t)
    ));
}

// IQ penumbra soft shadow. k controls softness: low (~2) = very soft, high (~16) = hard.
// ro should be offset by normal * small_epsilon to avoid self-intersection.
float soft_shadow(vec3 ro, vec3 rd, float mint, float maxt, float k) {
    float res = 1.0;
    float t   = mint;
    for (int i = 0; i < 48; ++i) {
        vec4  _t;
        float h = dist(ro + rd * t, _t);
        if (h < MINIMUM_HIT_DISTANCE) return SHADOW_MIN;
        res = min(res, k * h / t);
        t  += clamp(h, 0.005, 0.3);
        if (t > maxt) break;
    }
    return clamp(res, SHADOW_MIN, 1.0);
}

// ── Main ─────────────────────────────────────────────────────────────────────

void main() {
    vec2 uv = v_uv * 2.0 - 1.0;
    uv.y /= aspect;

    vec3  ray_origin    = vec3(0.0, 0.0, time - 4.0);
    vec3  ray_direction = normalize(vec3(uv, 1.0));

    vec4  trap;
    float t = ray_march(ray_origin, ray_direction, trap);

    // Orbit trap → single palette parameter
    // trap.x = final r, trap.y = min|w.y|, trap.z = min|w.z|
    float fy = 1.0 - smoothstep(0.0, 0.7, trap.y);
    float fz = 1.0 - smoothstep(0.0, 0.7, trap.z);
    float fx = smoothstep(2.0, 6.0, trap.x);
    float s  = clamp(fy * 0.30 + fz * 0.60 + fx * 0.4, 0.0, 1.0);

    const vec3 PA        = vec3(0.35, 0.26, 0.35);
    const vec3 PB        = vec3(0.28, 0.22, 0.25);
    const vec3 PC        = vec3(1.0,  0.9,  0.6);
    const vec3 PD        = vec3(0.05, 0.35, 0.6);
    const vec3 FOG_COLOR = vec3(0.04, 0.03, 0.06);

    if (t < MAXIMUM_TRACE_DISTANCE) {
        vec3  hit    = ray_origin + ray_direction * t;
        vec3  normal = calc_normal(hit);

        // Per-cell palette nudge to break tile uniformity
        float cell_h = cell_hash(floor(hit.xzy / CELL_SIZE));
        float sc     = clamp(s + cell_h * 0.22 - 0.11, 0.0, 1.0);
        vec3  col    = cosine_palette(sc, PA, PB, PC, PD);

        vec3  light  = mouse_light();
        float shadow = soft_shadow(hit + normal * 0.05, light, 0.05, 8.0, 6.0);
        vec3  view   = normalize(-ray_direction);
        vec3  shaded = phong(col, normal, light, view, 0.1, 48.0, shadow);

        shaded = mix(FOG_COLOR, shaded, exp(-t * 0.1));
        out_color = vec4(shaded, 1.0);
    } else {
        out_color = vec4(FOG_COLOR, 1.0);
    }
}
