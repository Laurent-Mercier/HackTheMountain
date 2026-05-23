#version 450

// Fragment uniform slot 0 → SPIR-V set=3, binding=0
layout(set = 3, binding = 0) uniform Uniforms {
    float time;
    float aspect;
    vec2  resolution;
    vec2  mouse;       // normalised [0,1], (0,0) = bottom-left
};

layout(location = 0) in  vec2 v_uv;
layout(location = 0) out vec4 out_color;


float SDF_sphere(vec3 pos, float radius) {
    return length(pos) - radius;
}

const float MAXIMUM_TRACE_DISTANCE = 1000.0;
const int NUMBER_OF_STEPS = 32;
const float MINIMUM_HIT_DISTANCE = 0.001;
vec4 ray_march(vec3 ro, vec3 rd) {
    float total_distance_traveled = 0.0;

    for (int i = 0; i < NUMBER_OF_STEPS; ++i)
    {
        vec3 current_pos = ro + rd * total_distance_traveled;
        float safe_distance = SDF_sphere(current_pos, 1.0);

        if (safe_distance < MINIMUM_HIT_DISTANCE) {
            return vec4(1.0, 0.0, 0.0, i);
        }

        else if (safe_distance > MAXIMUM_TRACE_DISTANCE) {
            break;
        }

        total_distance_traveled += safe_distance;
    }

    return vec4(0.0, 1.0, 0.0, 0.0);
}

void main() {
    // v_uv: (0,0) = bottom-left, (1,1) = top-right
    vec2 uv = v_uv * 2 - 1;
    uv.y = uv.y / aspect; 
    vec3 ray_origin = vec3(0.0, 0.0, -4.0);
    vec3 ray_direction = normalize(vec3(uv, 1.0));
    vec4 shaded_color = ray_march(ray_origin, ray_direction);
    float depth = shaded_color.w / (NUMBER_OF_STEPS);
    vec3 final_color = shaded_color.rgb;

    // Animated gradient starter — edit freely.
    out_color = vec4(final_color, 1.0);
}

