"""Amap proxy endpoints."""

import math

import requests
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .common import AMAP_API_BASE, AMAP_WEB_SERVICE_KEY


def index(request):
    """API home."""
    return JsonResponse({
        "name": "Safe Campus API",
        "version": "1.0",
        "status": "running",
        "endpoints": {
            "geocode": "/api/geocode",
            "regeo": "/api/regeo",
            "poi_search": "/api/poi/search",
            "route_plan": "/api/route/plan",
            "weather": "/api/weather",
            "district": "/api/district",
            "cameras_nearby": "/api/cameras/nearby",
            "reports": "/api/reports",
            "report_detail": "/api/reports/<id>",
            "report_upload": "/api/reports/upload",
            "report_penalty": "/api/reports/<id>/penalty",
            "auth_register": "/api/auth/register",
            "auth_login": "/api/auth/login",
            "auth_login_code": "/api/auth/login/code",
            "auth_me": "/api/auth/me",
            "admin_user_search": "/api/auth/admin/users/search?q=",
            "auth_update": "/api/auth/me/update",
            "auth_logout": "/api/auth/logout",
            "email_send": "/api/auth/email/send",
            "auth_password_reset": "/api/auth/password/reset",
            "rewards": "/api/rewards",
            "reward_catalog": "/api/rewards/catalog",
            "reward_redeem": "/api/rewards/redeem",
            "reward_adjust": "/api/rewards/adjust",
            "security_notify": "/api/security/notify",
            "dashboard_summary": "/api/dashboard/summary",
            "alerts": "/api/alerts",
            "alert_update_status": "/api/alerts/<report_id>/status",
            "tickets": "/api/tickets",
            "contact": "/api/contact",
            "detection_image_upload": "/api/detections/image",
            "detection_batch_upload": "/api/detections/batch",
            "detection_video_upload": "/api/detections/video",
            "detection_records": "/api/detections",
            "assistant_ask": "/api/assistant/ask",
            "assistant_health": "/api/assistant/health",
            "realtime_stream": "/api/realtime/stream",
            "realtime_poll": "/api/realtime/poll",
            "robot_status": "/api/robot/status",
            "robot_camera_frame": "/api/robot/camera/frame",
            "robot_recognitions": "/api/robot/recognitions",
            "robot_ingest_status": "/api/robot/ingest/status",
            "robot_ingest_frame": "/api/robot/ingest/frame",
            "robot_ingest_recognitions": "/api/robot/ingest/recognitions",
            "twin_overview": "/api/twin/overview",
            "dispatch": "/api/dispatch",
            "scenario_create": "/api/scenarios",
            "scenario_run": "/api/scenarios/<id>/run",
            "scenario_run_detail": "/api/scenarios/runs/<run_id>",
            "ai_metrics": "/api/ai/metrics",
            "ai_feedback": "/api/ai/feedback",
        },
    })


@require_http_methods(["GET"])
def geocode(request):
    """
    鍦扮悊缂栫爜锛氬湴鍧€杞潗鏍?
    鍙傛暟锛歛ddress - 鍦板潃锛堝锛氳タ瀹夊競闆佸鍖猴級
    杩斿洖锛氱粡绾害鍧愭爣
    """
    address = request.GET.get('address', '')

    if not address:
        return JsonResponse({"error": "缂哄皯 address 鍙傛暟"}, status=400)

    try:
        response = requests.get(f"{AMAP_API_BASE}/geocode/geo", params={
            "key": AMAP_WEB_SERVICE_KEY,
            "address": address
        })

        data = response.json()

        if data['status'] == '1' and data['geocodes']:
            location = data['geocodes'][0]['location'].split(',')
            return JsonResponse({
                "success": True,
                "address": address,
                "location": {
                    "lng": float(location[0]),
                    "lat": float(location[1])
                },
                "formatted_address": data['geocodes'][0].get('formatted_address', ''),
                "level": data['geocodes'][0].get('level', '')
            })
        else:
            return JsonResponse({
                "success": False,
                "error": "鏈壘鍒拌鍦板潃"
            }, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def regeo(request):
    """
    閫嗗湴鐞嗙紪鐮侊細鍧愭爣杞湴鍧€
    鍙傛暟锛歭ng - 缁忓害, lat - 绾害
    杩斿洖锛氳缁嗗湴鍧€淇℃伅
    """
    lng = request.GET.get('lng', '')
    lat = request.GET.get('lat', '')

    if not lng or not lat:
        return JsonResponse({"error": "缂哄皯 lng 鎴?lat 鍙傛暟"}, status=400)

    try:
        response = requests.get(f"{AMAP_API_BASE}/geocode/regeo", params={
            "key": AMAP_WEB_SERVICE_KEY,
            "location": f"{lng},{lat}"
        })

        data = response.json()

        if data['status'] == '1':
            regeocode = data['regeocode']
            return JsonResponse({
                "success": True,
                "location": {"lng": float(lng), "lat": float(lat)},
                "formatted_address": regeocode.get('formatted_address', ''),
                "province": regeocode['addressComponent'].get('province', ''),
                "city": regeocode['addressComponent'].get('city', ''),
                "district": regeocode['addressComponent'].get('district', ''),
                "street": regeocode['addressComponent'].get('street', ''),
                "streetNumber": regeocode['addressComponent'].get('streetNumber', '')
            })
        else:
            return JsonResponse({
                "success": False,
                "error": "鏈壘鍒拌鍧愭爣鐨勫湴鍧€"
            }, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def poi_search(request):
    """
    POI 鎼滅储锛氭悳绱㈠叴瓒ｇ偣
    鍙傛暟锛?
        keywords - 鎼滅储鍏抽敭璇嶏紙濡傦細鍥句功棣嗭級
        city - 鍩庡競锛堝彲閫夛紝濡傦細瑗垮畨锛?
        location - 涓績鐐瑰潗鏍囷紙鍙€夛紝鏍煎紡锛歭ng,lat锛?
        radius - 鎼滅储鍗婂緞锛堝彲閫夛紝鍗曚綅锛氱背锛岄粯璁?000锛?
    杩斿洖锛歅OI 鍒楄〃
    """
    keywords = request.GET.get('keywords', '')
    city = request.GET.get('city', '瑗垮畨')
    location = request.GET.get('location', '')
    radius = request.GET.get('radius', '3000')

    if not keywords:
        return JsonResponse({"error": "缂哄皯 keywords 鍙傛暟"}, status=400)

    try:
        params = {
            "key": AMAP_WEB_SERVICE_KEY,
            "keywords": keywords,
            "city": city
        }

        # 濡傛灉鎻愪緵浜嗕腑蹇冪偣锛屼娇鐢ㄥ懆杈规悳绱?
        if location:
            params['location'] = location
            params['radius'] = radius
            endpoint = f"{AMAP_API_BASE}/place/around"
        else:
            endpoint = f"{AMAP_API_BASE}/place/text"

        response = requests.get(endpoint, params=params)
        data = response.json()

        if data['status'] == '1':
            pois = []
            for poi in data.get('pois', []):
                location = poi['location'].split(',')
                pois.append({
                    "id": poi.get('id', ''),
                    "name": poi.get('name', ''),
                    "type": poi.get('type', ''),
                    "address": poi.get('address', ''),
                    "location": {
                        "lng": float(location[0]),
                        "lat": float(location[1])
                    },
                    "distance": poi.get('distance', ''),
                    "tel": poi.get('tel', '')
                })

            return JsonResponse({
                "success": True,
                "count": len(pois),
                "pois": pois
            })
        else:
            return JsonResponse({
                "success": False,
                "error": "鎼滅储澶辫触"
            }, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def route_plan(request):
    """
    璺緞瑙勫垝锛氳绠椾袱鐐逛箣闂寸殑璺嚎
    鍙傛暟锛?
        origin - 璧风偣鍧愭爣锛堟牸寮忥細lng,lat锛?
        destination - 缁堢偣鍧愭爣锛堟牸寮忥細lng,lat锛?
        type - 璺嚎绫诲瀷锛坵alking=姝ヨ, driving=椹捐溅, bicycling=楠戣锛岄粯璁?walking锛?
    杩斿洖锛氳矾绾夸俊鎭?
    """
    origin = request.GET.get('origin', '')
    destination = request.GET.get('destination', '')
    route_type = request.GET.get('type', 'walking')

    if not origin or not destination:
        return JsonResponse({"error": "缂哄皯 origin 鎴?destination 鍙傛暟"}, status=400)

    try:
        # 鏍规嵁绫诲瀷閫夋嫨涓嶅悓鐨?API
        if route_type == 'driving':
            endpoint = f"{AMAP_API_BASE}/direction/driving"
        elif route_type == 'bicycling':
            endpoint = f"{AMAP_API_BASE}/direction/bicycling"
        else:
            endpoint = f"{AMAP_API_BASE}/direction/walking"

        response = requests.get(endpoint, params={
            "key": AMAP_WEB_SERVICE_KEY,
            "origin": origin,
            "destination": destination
        })

        data = response.json()

        if data['status'] == '1':
            route_key = 'route'
            paths = data[route_key]['paths']

            if paths:
                path = paths[0]
                return JsonResponse({
                    "success": True,
                    "type": route_type,
                    "distance": path.get('distance', ''),  # 绫?
                    "duration": path.get('duration', ''),  # 绉?
                    "steps": len(path.get('steps', [])),
                    "path_data": path
                })

        return JsonResponse({
            "success": False,
            "error": "璺緞瑙勫垝澶辫触"
        }, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def weather(request):
    """
    澶╂皵鏌ヨ
    鍙傛暟锛歝ity - 鍩庡競锛堝锛氳タ瀹夛紝榛樿锛氳タ瀹夛級
    杩斿洖锛氬ぉ姘斾俊鎭?
    """
    city = request.GET.get('city', '瑗垮畨')

    try:
        response = requests.get(f"{AMAP_API_BASE}/weather/weatherInfo", params={
            "key": AMAP_WEB_SERVICE_KEY,
            "city": city,
            "extensions": "base"  # base=瀹炲喌澶╂皵, all=棰勬姤澶╂皵
        })

        data = response.json()

        if data['status'] == '1' and data.get('lives'):
            live = data['lives'][0]
            return JsonResponse({
                "success": True,
                "city": live.get('city', ''),
                "weather": live.get('weather', ''),
                "temperature": live.get('temperature', ''),
                "winddirection": live.get('winddirection', ''),
                "windpower": live.get('windpower', ''),
                "humidity": live.get('humidity', ''),
                "reporttime": live.get('reporttime', '')
            })
        else:
            return JsonResponse({
                "success": False,
                "error": "澶╂皵鏌ヨ澶辫触"
            }, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def district(request):
    """
    琛屾斂鍖哄煙鏌ヨ
    鍙傛暟锛?
        keywords - 鍖哄煙鍏抽敭璇嶏紙濡傦細闆佸鍖猴級
        subdistrict - 瀛愮骇琛屾斂鍖猴紙0=涓嶈繑鍥? 1=杩斿洖涓嬩竴绾? 2=杩斿洖涓嬩袱绾э紝榛樿 1锛?
    杩斿洖锛氳鏀垮尯鍩熶俊鎭?
    """
    keywords = request.GET.get('keywords', '西安市')
    subdistrict = request.GET.get('subdistrict', '1')

    try:
        response = requests.get(f"{AMAP_API_BASE}/config/district", params={
            "key": AMAP_WEB_SERVICE_KEY,
            "keywords": keywords,
            "subdistrict": subdistrict
        })

        data = response.json()

        if data['status'] == '1' and data.get('districts'):
            district_data = data['districts'][0]
            return JsonResponse({
                "success": True,
                "name": district_data.get('name', ''),
                "center": district_data.get('center', ''),
                "level": district_data.get('level', ''),
                "districts": district_data.get('districts', [])
            })
        else:
            return JsonResponse({
                "success": False,
                "error": "鍖哄煙鏌ヨ澶辫触"
            }, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def cameras_nearby(request):
    """
    鏌ユ壘闄勮繎鐨勭洃鎺х偣锛堢ず渚嬫帴鍙ｏ級
    鍙傛暟锛?
        lng - 缁忓害
        lat - 绾害
        radius - 鍗婂緞锛堢背锛岄粯璁?500锛?
    杩斿洖锛氶檮杩戠殑鐩戞帶鐐瑰垪琛?
    """
    lng = request.GET.get('lng', '')
    lat = request.GET.get('lat', '')
    radius = float(request.GET.get('radius', '500'))

    if not lng or not lat:
        return JsonResponse({"error": "缂哄皯 lng 鎴?lat 鍙傛暟"}, status=400)

    # 瑗垮畨宸ョ▼澶у涓存郊鏍″尯 - 鐩戞帶鐐规暟鎹?
    all_cameras = [
        {"id": 1, "name": "涓滈棬鐩戞帶", "lng": 109.189400, "lat": 34.366207, "status": "online"},
        {"id": 2, "name": "鍖楅棬鐩戞帶", "lng": 109.186563, "lat": 34.369237, "status": "online"},
        {"id": 3, "name": "图书馆监控", "lng": 109.186171, "lat": 34.366146, "status": "online"},
        {"id": 4, "name": "教学楼监控", "lng": 109.187773, "lat": 34.366634, "status": "online"},
        {"id": 5, "name": "瀛︾敓鍏瘬鐩戞帶", "lng": 109.183895, "lat": 34.363880, "status": "offline"},
        {"id": 6, "name": "椋熷爞鐩戞帶", "lng": 109.185200, "lat": 34.367500, "status": "online"},
        {"id": 7, "name": "南环体育场监控", "lng": 109.184392, "lat": 34.361558, "status": "online"},
        {"id": 8, "name": "东环体育场监控", "lng": 109.188055, "lat": 34.363273, "status": "online"},
        {"id": 9, "name": "学院楼监控", "lng": 109.188795, "lat": 34.368091, "status": "online"},
        {"id": 10, "name": "行政楼监控", "lng": 109.186559, "lat": 34.364108, "status": "online"}
    ]

    def calculate_distance(lng1, lat1, lng2, lat2):
        """璁＄畻涓ょ偣涔嬮棿鐨勮窛绂伙紙绫筹級"""
        dx = (lng2 - lng1) * 111000 * math.cos(math.radians(lat1))
        dy = (lat2 - lat1) * 111000
        return math.sqrt(dx*dx + dy*dy)

    nearby_cameras = []
    for camera in all_cameras:
        distance = calculate_distance(
            float(lng), float(lat),
            camera['lng'], camera['lat']
        )
        if distance <= radius:
            camera['distance'] = round(distance, 2)
            nearby_cameras.append(camera)

    # 鎸夎窛绂绘帓搴?
    nearby_cameras.sort(key=lambda x: x['distance'])

    return JsonResponse({
        "success": True,
        "count": len(nearby_cameras),
        "cameras": nearby_cameras
    })
