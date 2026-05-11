import axios from "axios";

export const http = axios.create({
    baseURL: import.meta.env.VITE_API_BASE_URL,
    timeout: 15_000,
});

http.interceptors.request.use((config) => {
    const token =
        localStorage.getItem("access_token") ?? import.meta.env.VITE_DEV_JWT_TOKEN;

    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }

    return config;
});