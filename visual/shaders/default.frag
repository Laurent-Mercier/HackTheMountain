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

void main() {
    // v_uv: (0,0) = bottom-left, (1,1) = top-right
    vec2 uv = v_uv;

    // Animated gradient starter — edit freely.
    vec3 col = vec3(uv, 0.5 + 0.5 * sin(time));
    out_color = vec4(col, 1.0);
}
