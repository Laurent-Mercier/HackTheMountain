// Created by SHAU - 2017
// License Creative Commons Attribution-NonCommercial-ShareAlike 3.0 Unported License.
//-----------------------------------------------------

/*
Based on Apollonian II by IQ and Apollonian structure by Shane
Turbulance fractal based on Marble by Guil
*/

#define FAR 20.
#define EPS 0.001
#define T iTime * 1.
#define R iResolution.xy
#define SD .46

mat2 rot(float x) {return mat2(cos(x), sin(x), -sin(x), cos(x));}

//IQ cosine palattes
//https://iquilezles.org/articles/palettes
vec3 PT(float t) {return vec3(.5) + vec3(.5) * cos(6.28318 * (vec3(1) * t * 0.1 + vec3(0, .33, .67)));}

vec3 tile(vec3 p) {
    return abs(mod(p, 2.) - 1.); // - vec3(1.);
}

float sdSphere(vec3 p, float r) {
    return length(p) - r;    
}

vec2 nearest(vec2 a, vec2 b){ 
    return mix(a, b, step(b.x, a.x));
}

vec3 map(vec3 p) {
    
    float scale = 1.;

    vec3 q = p;
    for (int i = 0; i < 8; i++) {
        q = mod(q - 1., 2.) - 1.;
        q -= sign(q) * (0.05 + sin(T * 0.14) * 0.02);
        float k = (1.1 + sin(T * 0.1) * -0.1) / dot(q, q);
        q *= k;
        scale *= k;
    }

    float t = (.25 * length(q) / scale);
    
    p = tile(p);
    float b = sdSphere(p - vec3(1), SD);
    
    return vec3(nearest(vec2(t, 1.), vec2(b, 2.)), b);
}

//tetrahedral normal
vec3 normal(vec3 p) {  
    vec2 e = vec2(-1., 1.) * EPS;   
	return normalize(e.yxx * map(p + e.yxx).x + e.xxy * map(p + e.xxy).x + 
					 e.xyx * map(p + e.xyx).x + e.yyy * map(p + e.yyy).x);   
}

//IQ - https://iquilezles.org/articles/raymarchingdf
float AO(vec3 p, vec3 n) {
    float ra = 0., w = 1., d = 0.;
    for (float i = 1.; i < 5.; i += 1.){
        d = i / 5.;
        ra += w * (d - map(p + n * d).x);
        w *= .5;
    }
    return 1. - clamp(ra, 0., 1.);
}

//fractal from GUIL
//https://www.shadertoy.com/view/MtX3Ws
vec2 csqr(vec2 a) {return vec2(a.x * a.x - a.y * a.y, 2.0 * a.x * a.y);}

float fractal(vec3 p) {
	
	float res = 0.0;
	float x = .7;
    
    p = tile(p);
    p.yz *= rot(T * .6);
    
    vec3 c = p;
	
    for (int i = 0; i < 10; ++i) {
        p = x * abs(p) / dot(p, p) - x;
        p.yz = csqr(p.yz);
        p = p.zxy;
        res += exp(-19. * abs(dot(p, c)));   
	}
    return res / 2.;
}

float fractalMarch(vec3 ro, vec3 rd) {
    
    float c = 0., t = EPS;
    
    for (int i = 0; i < 50; i++) {
        
        vec3 p = ro + t * rd;
        
        vec3 q = tile(p);
        float b = sdSphere(q - vec3(1), SD);
        if (b > EPS) break;
        
        float bc = sdSphere(q - vec3(1), .01);
        bc = 1. / (1. + bc * bc * 20.);
        
        float fs = fractal(p); 
        t += 0.02 * exp(-2.0 * fs);
        
        c += 0.04 * bc;
    } 
    
    return c;
}
vec3 render(vec3 ro, vec3 rd) {
    
    float mint = FAR;
    
    vec3 pc = vec3(0), bg = pc, gc = PT(T), p = pc;
    vec3 ld = normalize(vec3(3., 4., -1.));
    
    //ray marching
    float t = 0., id = 0.;
    for (int i = 0; i < 96; i++) {
        p = ro + rd * t;
        vec3 ns = map(p);
        if (ns.x < EPS || t > FAR) {
            id = ns.y;
            break;
        }
        
        float lt = 1. / (1. + ns.z * ns.z * 140.);
        bg += gc * lt * 0.03;
        
        t += ns.x;
    }
    
    //*
    if (id > 0.) {
        
        mint = t;
        
        vec3 n = normal(p);
        float ao = AO(p, n);
        float dif = max(dot(ld, n), 0.05);
        float spc = pow(max(dot(reflect(-ld, n), -rd), 0.), 32.);
        float frs = pow(clamp(dot(n, rd) + 1., 0., 1.), 2.);
        
        if (id == 1.) {
            
            //apollonian
            pc = vec3(0.1) * dif;
            pc += vec3(0.1, 0.2, 0.4) * max(n.y, 0.);
            pc += gc * 0.6 * spc;
        }
        
        if (id == 2.) {
            
            //ball
            pc = gc * dif * 0.4;   
            pc += gc * fractalMarch(p, rd) * (1. - frs) * .6;
            pc += vec3(1) * spc; 
            frs = pow(clamp(dot(n, rd) + 1., 0., 1.), 2.) * 64.; 
            pc += gc * frs * 0.04 * dif; 
        }        
        
        pc *= ao;
    }
    //*/
    
    pc += bg;
    pc *= exp(-0.2 * mint);
    
    return pc * 1.6;
}

void camera(vec2 U, inout vec3 ro, inout vec3 rd, inout vec3 la) {
    
    vec2 uv = (U - R * .5) / R.y;
    
    ro = la - vec3(0, sin(T * 0.2) * 0.3, -3.0); 
    ro.xz *= rot(T * 0.1);
    
    vec3 fwd = normalize(la - ro);
    vec3 rgt = normalize(vec3(fwd.z, 0., -fwd.x)); 

    rd = normalize(fwd + 1.4 * uv.x * rgt + 1.4 * uv.y * cross(fwd, rgt));
}

void mainImage(out vec4 C, vec2 U) {

    vec3 ro, rd, la = vec3(-1, 1, -2);
    camera(U, ro, rd, la);
    
    vec3 pc = render(ro, rd);
    
    C = vec4(pc,1.0);
}

void mainVR(out vec4 C, vec2 U, vec3 fro, vec3 frd) {    
    
    vec3 ro = fro + vec3(1, 1, T * -0.2); //camera
    vec3 pc = render(ro, frd);
	C = vec4(pc, 1.);
}
