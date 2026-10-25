#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
geo.py — 坐标系与地理计算

为什么必须有这个文件：
国内地图（高德/腾讯/百度）用 GCJ-02 或 BD-09，OSM/Valhalla（真实路网拓扑）用 WGS-84。
两者在北京市区差 500~600 米。不转换就会把景点放到隔壁街区、路网耗时全算错。
"""
import math

A = 6378245.0
EE = 0.00669342162296594323
X_PI = math.pi * 3000.0 / 180.0


def out_of_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _tf_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _tf_lng(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng, lat):
    """WGS-84 → GCJ-02（OSM 坐标 → 高德/腾讯坐标）"""
    if out_of_china(lng, lat):
        return lng, lat
    dlat = _tf_lat(lng - 105.0, lat - 35.0)
    dlng = _tf_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = 1 - EE * math.sin(radlat) ** 2
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng, lat):
    """GCJ-02 → WGS-84（高德坐标 → OSM 坐标），迭代反解精度 <0.1m"""
    if out_of_china(lng, lat):
        return lng, lat
    mlng, mlat = lng, lat
    for _ in range(30):
        glng, glat = wgs84_to_gcj02(mlng, mlat)
        dlng, dlat = glng - lng, glat - lat
        if abs(dlng) < 1e-10 and abs(dlat) < 1e-10:
            break
        mlng -= dlng
        mlat -= dlat
    return mlng, mlat


def gcj02_to_bd09(lng, lat):
    """GCJ-02 → BD-09（百度坐标）"""
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * X_PI)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * X_PI)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def bd09_to_gcj02(lng, lat):
    x, y = lng - 0.0065, lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09(lng, lat):
    return gcj02_to_bd09(*wgs84_to_gcj02(lng, lat))


def bd09_to_wgs84(lng, lat):
    return gcj02_to_wgs84(*bd09_to_gcj02(lng, lat))


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def decode_polyline(encoded: str, precision: int = 6):
    """Valhalla / Google polyline 解码，返回 [(lat,lng), ...]"""
    coords = []
    index = lat = lng = 0
    factor = 10 ** precision
    n = len(encoded)
    while index < n:
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append((lat / factor, lng / factor))
    return coords
