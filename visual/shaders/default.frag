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
    vec2  _pad;
    vec4  chroma[3];     // 12 pitch-class energies packed as 3×vec4
                         // chroma[0].xyzw = C C# D D#, [1] = E F F# G, [2] = G# A A# B
} audio;

layout(location = 0) in  vec2 v_uv;
layout(location = 0) out vec4 out_color;

mat2 rot(float x) {return mat2(cos(x), sin(x), -sin(x), cos(x));}
vec3 palette(float t) {return vec3(.5) + vec3(.5) * cos(6.28318 * (vec3(1) * t * 0.1 + vec3(0, .33, .67)));}

float get_time() {
    return time *0.5;
}

vec3 tile(vec3 p) {
    return abs(mod(p, 2.) - 1.);
}

float SDF_sphere(vec3 p, float r) {
    return length(p) - r;
}

vec2 nearest(vec2 a, vec2 b) {
    return mix(a, b, step(b.x, a.x));
}

const float MAXIMUM_TRACE_DISTANCE = 1000.0;
const int   NUMBER_OF_STEPS        = 128;
const float MINIMUM_HIT_DISTANCE   = 0.001;
const float EPS = 0.001;
const float SD  = 0.46;

vec3 map(vec3 p) {
    float scale = 1.;
    vec3 displaced = p;
    // Bass breathing: expand/contract the tiling grid
    float breath = scale * (1.0 + audio.bass * 0.8);
    
    // Mid-frequency ripples
    displaced.x += sin(time * 0.5 + p.y * 2.0) * audio.mid * 0.25;
    displaced.y += cos(time * 0.7 + p.z * 2.0) * audio.mid * 0.25;
    
    // Treble adds high-frequency distortion
    displaced += vec3(
        sin(p.x * 8.0 + time * 2.0),
        cos(p.y * 8.0 - time * 1.5),
        sin(p.z * 8.0 + time * 1.8)
    ) * audio.treble * 0.08;

    vec3 q = p;
    for (int i = 0; i < 8; i++) {
        q = mod(q - 1., 2.) - 1.;
        q -= sign(q) * (0.05 + sin(get_time() * 0.14) * 0.02 + (1.0 - audio.smoothness) * 0.012);
        float k = (1.1 + sin(get_time() * 0.1) * -0.1) / dot(q, q);
        q *= k;
        scale *= k;
    }

    float t = (.25 * length(q) / scale);

    p = tile(p);
    float b = SDF_sphere(p - vec3(1), SD + audio.bass * 0.05);

    return vec3(nearest(vec2(t, 1.), vec2(b, 2.)), b);
}

vec3 normal(vec3 p) {
    vec2 e = vec2(-1., 1.) * 0.001;
    return normalize(e.yxx * map(p + e.yxx).x + e.xxy * map(p + e.xxy).x +
                     e.xyx * map(p + e.xyx).x + e.yyy * map(p + e.yyy).x);
}

vec2 csqr(vec2 a) {return vec2(a.x * a.x - a.y * a.y, 2.0 * a.x * a.y);}

float fractal(vec3 p) {
	
	float res = 0.0;
	float x = 0.7 + (1.0 - audio.smoothness) * 0.07;

    p = tile(p);
    p.yz *= rot(get_time() * .6);

    vec3 c = p;
	
    for (int i = 0; i < 10; ++i) {
        p = x * abs(p) / dot(p, p) - x;
        p.yz = csqr(p.yz);
        p = p.zxy;
        res += exp(-19. * abs(dot(p, c)));   
	}
    return res / 2.;
}

float fractal_march(vec3 ro, vec3 rd) {
    float c = 0., t = EPS;
    for (int i = 0; i < 50; i++) {
        vec3 p = ro + t * rd;
        vec3 q = tile(p);
        float b = SDF_sphere(q - vec3(1), SD + audio.bass * 0.05);
        if (b > EPS) break;
        float bc = SDF_sphere(q - vec3(1), .01);
        bc = 1. / (1. + bc * bc * 20.);
        float fs = fractal(p); 
        t += 0.02 * exp(-2.0 * fs);

        c += 0.04 * bc;
    } 
    return c;
}

vec3 render(vec3 ro, vec3 rd) {
    float traveled = 0.0;
    float id = 0.0;
    vec3 p = vec3(0.);

    float t = MAXIMUM_TRACE_DISTANCE;

    float vol = clamp((audio.volume_db + 80.0) / 80.0, 0.0, 1.0);

    // Color driven by harmonic content: average energy per pitch group
    float chroma_low  = dot(audio.chroma[0], vec4(0.25)); // C C# D D#
    float chroma_mid_c = dot(audio.chroma[1], vec4(0.25)); // E F F# G
    vec3 base_tint = palette(time * 0.5 + chroma_low * 2.5 + chroma_mid_c * 1.5);

    vec3 light_dir = normalize(vec3(3., 4., -1.));
    vec3 background = vec3(0.);

    vec3 color = vec3(0.);
    for (int i = 0; i < NUMBER_OF_STEPS; ++i) {
        p = ro + rd * traveled;
        vec3 nearest = map(p);
        if (traveled > MAXIMUM_TRACE_DISTANCE || nearest.x < MINIMUM_HIT_DISTANCE) {
            id = nearest.y;
            break;
        }

        float glow = 1. / (1. + nearest.z * nearest.z * 140.);
        background += base_tint * glow * 0.03;

        traveled += nearest.x;
    }

    if (id >0.0) {
        t = traveled;

        vec3 n = normal(p);
        float diffuse = max(dot(light_dir, n), 0.05);
        float specular = pow(max(dot(reflect(-light_dir, n), -rd), 0.), 32.);
        float fresner = pow(clamp(dot(n, rd)+ 1., 0., 1.), 2.);
        if (id == 1.) {
            color = vec3(0.1) * diffuse;
            color += vec3(0.1, 0.2, 0.4) * max(n.y, 0.);
            color += base_tint * (0.4 + audio.treble * 0.5) * specular;
        }

        if (id == 2.) {
            color = base_tint * diffuse * 0.4;
            color += base_tint * fractal_march(p, rd) * (1. - fresner) * (0.4 + audio.bass * 0.35);
            color += vec3(1) * specular;
            fresner = pow(clamp(dot(n, rd) +1., 0., 1.), 2.) * 64.;
            color += base_tint * fresner * 0.04 * diffuse;
        }
    }

    color += background;
    color *= exp(-0.2 * t);

    return color * (0.8 + vol);
}

void camera(vec2 uv, inout vec3 ro, inout vec3 rd, inout vec3 lookat) {
    ro = lookat - vec3(0, sin(time * 0.2) * 0.3, -3.0 - audio.mid * 0.25);
    ro.xz *= rot(time * 0.1);

    vec3 fwd = normalize(lookat - ro);
    vec3 rgt = normalize(vec3(fwd.z, 0., -fwd.x)); 

    rd = normalize(fwd + 1.4 * uv.x * rgt + 1.4 * uv.y * cross(fwd, rgt));
}



void main() {
    vec2 uv = (v_uv - 0.5) * vec2(aspect, 1.0);

    vec3 ro, rd, lookat = vec3(-1., 1., -2.);
    camera(uv, ro, rd, lookat);

    out_color = vec4(render(ro, rd), 1.0);
}
