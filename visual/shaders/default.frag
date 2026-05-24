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

mat2 rot(float x) {return mat2(cos(x), sin(x), -sin(x), cos(x));}
vec3 palette(float t) {return vec3(.5) + vec3(.5) * cos(6.28318 * (vec3(1) * t * 0.1 + vec3(0, .33, .67)));}

vec3 tile(vec3 p) {
    return abs(mod(p, 2.), -1.);
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

vec3 map(vec3 p) {
    float scale = 1.;

    vec3 q = p;
    for (int i = 0; i < 8; i++) {
        q = mod(q - 1., 2.) - 1.;
        q -= sign(q) * (0.05 + sin(time * 0.14) * 0.02);
        float k = (1.1 + sin(time * 0.1) * -0.1) / dot(q, q);
        q *= k;
        scale *= k;
    }

    float t = (.25 * length(q) / scale);

    p = tile(p);
    float b = SDF_sphere(p - vec3(1), 0.46);

    return vec3(nearest(vec2(t, 1.), vec2(b, 2.)), b);
}

vec3 normal(vec3 p) {
    const float e = 0.001;
    vec4 _t;
    return normalize(vec3(
        dist(p + vec3(e, 0, 0), _t) - dist(p - vec3(e, 0, 0), _t),
        dist(p + vec3(0, e, 0), _t) - dist(p - vec3(0, e, 0), _t),
        dist(p + vec3(0, 0, e), _t) - dist(p - vec3(0, 0, e), _t)
    ));
}

vec2 csqr(vec2 a) {return vec2(a.x * a.x - a.y * a.y, 2.0 * a.x * a.y);}

float fractal(vec3 p) {
	
	float res = 0.0;
	float x = .7;

    p = tile(p);
    p.yz *= rot(time * .6);

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
        float b = SDF_sphere(q - vec3(1), SD);
        if (b > EPS) break;
        float bc = SDF_Sphere(q - vec3(1), .01);
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

    vec3 light_dir = normalize(vec3(3., 4., -1.));
    vec3 base_tint = palette(time);
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
        bg += base_tint * glow * 0.03;

        traveled += d;
    }

    if (id >0.0) {
        t = traveled;

        vec3 n = normal(p);
        float diffuse = max(dot(light_dir, n), 0.05);
        float specular = pow(max(dot(reflect(-light_dir, n), -rd), 0.), 32.);
        float fresner = pow(clamp(dot(n, rd)+ 1., 0., 1.), 2.);
        if (id == 1.) {
            color = vec3(0.1) * diffuse;
            color += vec3(0.1, 0.2, 0.4) * max(n.y, 0);
            color += base_tint * 0.6 * specular;
        }

        if (id == 2.) {
            color = base_tint * diffuse * 0.4;
            color += base_tint * fractal_march(p, rd) * (1. - fresner) * 0.6;
            color += vec3(1) * specular;
            fresner = pow(clamp(dot(n, rd) +1., 0., 1.), 2.) * 64.;
            color += base_tint * fresner * 0.04 * diffuse;
        }
    }

    color += background;
    color *= exp(-0.2 * t);

    return color * 1.6;
}

void camera(vec2 uv, inout vec3 ro, inout vec3 rd, inout vec3 lookat) {
    ro = lookat - vec3(0, sin(time * 0.2) * 0.3, -3.0); 
    ro.xz *= rot(time * 0.1);

    vec3 fwd = normalize(lookat - ro);
    vec3 rgt = normalize(vec3(fwd.z, 0., -fwd.x)); 

    rd = normalize(fwd + 1.4 * uv.x * rgt + 1.4 * uv.y * cross(fwd, rgt));
}


float soft_shadow(vec3 ro, vec3 rd, float tmin, float tmax, float k) {
    float res = 1.0;
    float t   = tmin;
    for (int i = 0; i < 32 && t < tmax; ++i) {
        vec4 _t;
        float h = dist(ro + rd * t, _t);
        res = min(res, k * h / t);
        if (res < 0.001) break;
        t += clamp(h, 0.02, 0.2);
    }
    return clamp(res, 0.0, 1.0);
}




void main() {
    vec2 uv = v_uv * 2.0 - 1.0;
    uv.y /= aspect;

    // Orbiting look-at camera circling the fractal
    float cam_r         = 2.8 + 0.2 * cos(time * 0.17);
    vec3  ray_origin    = cam_r * vec3(cos(time * 0.13), 0.35 * sin(time * 0.09), sin(time * 0.13));
    vec3  ww            = normalize(-ray_origin);
    vec3  uu            = normalize(cross(ww, vec3(0.0, 1.0, 0.0)));
    vec3  vv            = cross(uu, ww);
    vec3  ray_direction = normalize(uv.x * uu + uv.y * vv + 1.5 * ww);

    vec4  trap;
    float t = ray_march(ray_origin, ray_direction, trap);

    // Orbit trap → single palette parameter
    float fy = 1.0 - smoothstep(0.0, 0.7, trap.y);
    float fz = 1.0 - smoothstep(0.0, 0.7, trap.z);
    float fx = smoothstep(2.0, 6.0, trap.x);
    float s  = clamp(fy * 0.30 + fz * 0.60 + fx * 0.4, 0.0, 1.0);

    const vec3 PA        = vec3(0.55, 0.50, 0.65);
    const vec3 PB        = vec3(0.45, 0.45, 0.35);
    const vec3 PC        = vec3(1.0,  0.8,  0.6);
    const vec3 PD        = vec3(0.00, 0.33, 0.67);
    const vec3 FOG_COLOR = vec3(0.05, 0.04, 0.12);

    if (t < MAXIMUM_TRACE_DISTANCE) {
        vec3  hit    = ray_origin + ray_direction * t;
        vec3  normal = calc_normal(hit);

        vec3  col   = palette(s);
        vec3  light = mouse_light();
        vec3  view  = normalize(-ray_direction);

        float shadow = soft_shadow(hit + normal * 0.05, light, 0.05, 8.0, 6.0);
        float occ    = clamp(0.05 * log(trap.x + 1.0), 0.0, 1.0);
        float rim    = clamp(1.0 + dot(ray_direction, normal), 0.0, 1.0);

        float diff = max(dot(normal, light), 0.0) * shadow;
        vec3  refl = reflect(-light, normal);
        float spec = pow(max(dot(refl, view), 0.0), 48.0) * shadow;

        vec3  fill  = normalize(vec3(-light.x, -0.5, -light.z));
        float diff2 = clamp(0.4 + 0.6 * dot(fill, normal), 0.0, 1.0) * occ;

        vec3 lin = vec3(0.0);
        lin += 1.5 * vec3(1.0, 1.0, 1.0) * diff;
        lin += 0.5 * vec3(0.2, 0.3, 0.4) * diff2;
        lin += 0.4 * vec3(0.1, 0.2, 0.3) * (0.5 + 0.5 * normal.y) * (0.2 + 0.8 * occ);
        lin += 0.4 * vec3(0.6, 0.8, 1.0) * rim * rim;

        vec3 shaded = col * lin;
        shaded = pow(max(shaded, vec3(0.0)), vec3(0.8, 0.9, 1.0));
        shaded += vec3(0.9) * spec * 4.0;

        shaded = mix(FOG_COLOR, shaded, exp(-t * 0.04));
        shaded = pow(max(shaded, vec3(0.0)), vec3(0.4545));
        out_color = vec4(shaded, 1.0);
    } else {
        // Sky gradient: dark base with sun glow toward mouse light
        vec3 sky = FOG_COLOR * (0.7 + 0.3 * ray_direction.y);
        sky += vec3(0.3, 0.2, 0.1) * pow(max(dot(ray_direction, mouse_light()), 0.0), 6.0);
        out_color = vec4(pow(max(sky, vec3(0.0)), vec3(0.4545)), 1.0);
    }
}
