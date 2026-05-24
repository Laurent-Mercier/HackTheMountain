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

const float MAXIMUM_TRACE_DISTANCE = 1000.0;
const int   NUMBER_OF_STEPS        = 128;
const float MINIMUM_HIT_DISTANCE   = 0.001;

const float CELL_SIZE              = 2.0;
const int   N_ITER                 = 16;
const int   EXP                    = 8;

float dist(vec3 p, out vec4 trap) {
    p.xyz = mod(p.xzy, CELL_SIZE) - 0.5 * CELL_SIZE;
    vec3 w = p;
    float r = 0.0;
    float dr = 1.0;

    trap = vec4(abs(w), r);

    for (int i = 0; i < N_ITER; ++i) {
        r = length(w);
        if (r > 2.0) break;
        float phi = atan(w.y, w.x);
        float theta = acos(w.z/r);

        float n_theta = EXP * theta;
        float n_phi = EXP * phi;

        dr = pow(r, EXP - 1.0) * EXP * dr + 1.0;
        w = p + pow(r, EXP) * vec3(sin(n_theta)*cos(n_phi), sin(n_theta)*sin(n_phi), cos(n_theta));
        trap = min(trap, vec4(abs(w), r));
    }

    trap = vec4(r, trap.yzw);

    return 0.5 * log(r) * r / dr;
}

float ray_march(vec3 ro, vec3 rd, out vec4 trap) {
    float traveled = 0.0;
    for (int i = 0; i < NUMBER_OF_STEPS; ++i) {
        float distance = dist(ro + rd * traveled, trap);
        if (distance < MINIMUM_HIT_DISTANCE) return traveled;
        if (traveled > MAXIMUM_TRACE_DISTANCE) break;
        traveled += distance;
    }
    return MAXIMUM_TRACE_DISTANCE;
}

vec3 calc_normal(vec3 p) {
    const float e = 0.001;
    vec4 _trash;
    return normalize(vec3(
        dist(p + vec3(e, 0, 0), _trash) - dist(p - vec3(e, 0, 0), _trash),
        dist(p + vec3(0, e, 0), _trash) - dist(p - vec3(0, e, 0), _trash),
        dist(p + vec3(0, 0, e), _trash) - dist(p - vec3(0, 0, e), _trash)
    ));
}

void main() {
    // v_uv: (0,0) = bottom-left, (1,1) = top-right
    vec2 uv = v_uv * 2.0 - 1.0;
    uv.y /= aspect;

    vec3 ray_origin    = vec3(0.0, 0.0, log(time)-4.0);
    vec3 ray_direction = normalize(vec3(uv, 1.0));

    vec4 trap;
    float t = ray_march(ray_origin, ray_direction, trap);

    // trap.x = final r, trap.y = min|w.y|, trap.z = min|w.z|
    float fy = 1.0 - smoothstep(0.0, 0.7, trap.y);
    float fz = 1.0 - smoothstep(0.0, 0.7, trap.z);
    float fx = smoothstep(2.0, 6.0, trap.x);

    // Single blended parameter drives a cosine palette → bounded brightness, no stacking
    float s = clamp(fy * 0.55 + fz * 0.35 + fx * 0.1, 0.0, 1.0);

    // Cosine palette: bumped baseline (a) and amplitude (b)
    // Sweeps: deep purple → dark teal → rust → ochre (brighter)
    vec3 a = vec3(0.35, 0.26, 0.35); // Raised the baseline average
    vec3 b = vec3(0.28, 0.22, 0.25); // Raised the contrast range
    vec3 c = vec3(1.0,  0.9,  0.6);  // Kept frequency the same
    vec3 d = vec3(0.05, 0.35, 0.6);  // Kept phase the same
    vec3 col = a + b * cos(6.28318 * (c * s + d));

    if (t < MAXIMUM_TRACE_DISTANCE) {
        vec3 hit    = ray_origin + ray_direction * t;
        vec3 normal = calc_normal(hit);

        vec3 light  = normalize(vec3(mouse * 2.0 - 1.0, -1.0));
        float diff  = max(dot(normal, light), 0.0);

        vec3 view   = normalize(-ray_direction);
        vec3 refl   = reflect(-light, normal);
        float spec  = pow(max(dot(refl, view), 0.0), 48.0);

        // Tinted specular keeps highlights in palette; no gamma to avoid further brightening
        vec3 shaded = col * (0.1 + 0.85 * diff) + col * 0.6 * spec;
        out_color   = vec4(shaded, 1.0);
    } else {
        out_color = vec4(0.67, 0.84, 0.91, 1.0);
    }
}
