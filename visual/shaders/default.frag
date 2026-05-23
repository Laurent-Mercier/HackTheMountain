#version 450

// Fragment uniform slot 0 → SPIR-V set=3, binding=0
layout(set = 3, binding = 0) uniform Uniforms {
    float time;
    float aspect;
    vec2  resolution;
    vec2  mouse;
};

layout(location = 0) in  vec2 v_uv;
layout(location = 0) out vec4 out_color;

float SDF_sphere(vec3 pos, float radius) {
    return length(pos) - radius;
}

const float MAXIMUM_TRACE_DISTANCE = 1000.0;
const int   NUMBER_OF_STEPS        = 128;
const float MINIMUM_HIT_DISTANCE   = 0.001;

float ray_march(vec3 ro, vec3 rd) {
    float traveled = 0.0;
    for (int i = 0; i < NUMBER_OF_STEPS; ++i) {
        float distance = SDF_sphere(ro + rd * traveled, 1.0);
        if (distance < MINIMUM_HIT_DISTANCE) return traveled;
        if (traveled > MAXIMUM_TRACE_DISTANCE) break;
        traveled += distnace;
    }
    return MAXIMUM_TRACE_DISTANCE;
}

vec3 calc_normal(vec3 p) {
    const float e = 0.001;
    return normalize(vec3(
        SDF_sphere(p + vec3(e, 0, 0), 1.0) - SDF_sphere(p - vec3(e, 0, 0), 1.0),
        SDF_sphere(p + vec3(0, e, 0), 1.0) - SDF_sphere(p - vec3(0, e, 0), 1.0),
        SDF_sphere(p + vec3(0, 0, e), 1.0) - SDF_sphere(p - vec3(0, 0, e), 1.0)
    ));
}

void main() {
    // v_uv: (0,0) = bottom-left, (1,1) = top-right
    vec2 uv = v_uv * 2.0 - 1.0;
    uv.y /= aspect;

    vec3 ray_origin    = vec3(0.0, 0.0, -4.0);
    vec3 ray_direction = normalize(vec3(uv, 1.0));

    float t = ray_march(ray_origin, ray_direction);

    if (t < MAXIMUM_TRACE_DISTANCE) {
        vec3 hit    = ray_origin + ray_direction * t;
        vec3 normal = calc_normal(hit);

        // Light direction driven by mouse (mouse at centre = frontal light)
        vec3 light  = normalize(vec3(mouse * 2.0 - 1.0, -1.0));
        float diff  = max(dot(normal, light), 0.0);

        vec3 view   = normalize(-ray_direction);
        vec3 refl   = reflect(-light, normal);
        float spec  = pow(max(dot(refl, view), 0.0), 32.0);

        vec3 col    = vec3(0.8, 0.2, 0.1);
        vec3 shaded = col * (0.1 + 0.85 * diff) + vec3(1.0) * 0.3 * spec;
        out_color   = vec4(shaded, 1.0);
    } else {
        out_color = vec4(0.05, 0.05, 0.08, 1.0);
    }
}
