import requests
import datetime
import json
import logging

logger = logging.getLogger(__name__)

import os

def refresh_google_token(refresh_token: str) -> str:
    """
    Renueva el access_token usando el refresh_token si las credenciales existen.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    if not refresh_token or not client_id or not client_secret:
        return None
        
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    try:
        resp = requests.post(url, data=payload)
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception as e:
        logger.error(f"Error refreshing Google Token: {e}")
    return None

def check_calendar_availability(access_token: str, refresh_token: str, date_str: str) -> str:
    """
    Revisa la disponibilidad en Google Calendar para una fecha dada (YYYY-MM-DD).
    """
    try:
        # Definir el inicio y fin del día
        start_of_day = f"{date_str}T00:00:00Z"
        end_of_day = f"{date_str}T23:59:59Z"
        
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events"
        params = {
            "timeMin": start_of_day,
            "timeMax": end_of_day,
            "singleEvents": True,
            "orderBy": "startTime"
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
        
        resp = requests.get(url, headers=headers, params=params)
        
        # Auto refresh si el token expiró
        if resp.status_code == 401 and refresh_token:
            new_token = refresh_google_token(refresh_token)
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                resp = requests.get(url, headers=headers, params=params)
                
        if resp.status_code == 401:
            return "Error: Token expirado o inválido. No se puede acceder al calendario en este momento. Dile al cliente que te comunique con un ejecutivo para agendar."
        if resp.status_code != 200:
            return f"Error al consultar el calendario: {resp.text}"
            
        events = resp.json().get("items", [])
        
        if not events:
            return f"El calendario está completamente libre el {date_str}."
            
        busy_slots = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            busy_slots.append(f"- De {start} a {end}")
            
        return f"Eventos programados para el {date_str}:\n" + "\n".join(busy_slots) + "\n\nSugiere horarios que no se superpongan con estos eventos."
        
    except Exception as e:
        logger.error(f"Error checking calendar: {e}")
        return "Error interno al revisar el calendario."

def book_calendar_appointment(access_token: str, refresh_token: str, start_time: str, end_time: str, summary: str, description: str, attendee_email: str = None, kam_email: str = None) -> str:
    """
    Agenda una reunión en Google Calendar.
    start_time y end_time deben ser en formato ISO 8601 (ej. 2023-10-25T10:00:00-03:00).
    """
    try:
        url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        event = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start_time,
            },
            "end": {
                "dateTime": end_time,
            },
            "conferenceData": {
                "createRequest": {
                    "requestId": f"meet-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}
                }
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 60},
                    {"method": "popup", "minutes": 15}
                ]
            }
        }
        
        attendees = []
        if attendee_email and attendee_email.strip():
            attendees.append({"email": attendee_email.strip()})
        if kam_email and kam_email.strip() and kam_email.strip().lower() != (attendee_email or "").strip().lower():
            attendees.append({"email": kam_email.strip()})
            
        if attendees:
            event["attendees"] = attendees
            
        params = {"sendUpdates": "all", "conferenceDataVersion": 1}
        resp = requests.post(url, headers=headers, params=params, json=event)
        
        if resp.status_code == 401 and refresh_token:
            new_token = refresh_google_token(refresh_token)
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                resp = requests.post(url, headers=headers, params=params, json=event)
                
        if resp.status_code == 401:
            return "Error: Token expirado o inválido. No se pudo agendar. Deriva a un ejecutivo para continuar."
            
        if resp.status_code in [200, 201]:
            data = resp.json()
            link = data.get("htmlLink", "")
            meet_link = data.get("hangoutLink", "")
            msg = f"Reunión agendada exitosamente en Google Calendar. Se envió la invitación por correo formal con enlace de Google Meet a {attendee_email or 'el lead'}."
            if meet_link:
                msg += f" Enlace de Google Meet: {meet_link}"
            return msg
            
        return f"Fallo al agendar la reunión: {resp.text}"
        
    except Exception as e:
        logger.error(f"Error booking appointment: {e}")
        return "Error interno al intentar agendar la reunión."


def sync_kam_attendee_to_calendar(access_token: str, refresh_token: str, kam_email: str, lead_name: str = "", lead_phone: str = "") -> dict:
    """
    Busca el próximo evento agendado del lead en Google Calendar y agrega el correo del KAM
    como invitado (attendee) junto con recordatorios automáticos pre-reunión.
    """
    if not access_token or not kam_email or not kam_email.strip():
        return {"synced": False, "message": "Faltan credenciales de Google Calendar o correo del KAM"}

    clean_kam_email = kam_email.strip().lower()
    try:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        list_url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        params = {
            "timeMin": now_iso,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 25,
        }
        resp = requests.get(list_url, headers=headers, params=params, timeout=15)
        if resp.status_code == 401 and refresh_token:
            new_token = refresh_google_token(refresh_token)
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                resp = requests.get(list_url, headers=headers, params=params, timeout=15)

        if resp.status_code != 200:
            return {"synced": False, "message": f"Error consultando Google Calendar ({resp.status_code})"}

        events = resp.json().get("items", [])
        if not events:
            return {"synced": False, "message": "No se encontraron eventos futuros en Google Calendar"}

        digits_phone = "".join(ch for ch in (lead_phone or "") if ch.isdigit())
        short_phone = digits_phone[-8:] if len(digits_phone) >= 8 else digits_phone
        clean_lead_name = (lead_name or "").strip().lower()
        ignore_names = {"", "usuario desconocido", "unknown", "desconocido"}

        target_event = None
        for ev in events:
            blob = f"{ev.get('summary', '')} {ev.get('description', '')}".lower()
            blob_digits = "".join(ch for ch in blob if ch.isdigit())
            if short_phone and short_phone in blob_digits:
                target_event = ev
                break
            if clean_lead_name not in ignore_names and clean_lead_name in blob:
                target_event = ev
                break

        # Fallback: evento más próximo que tenga Google Meet o palabras clave de asesoría/reunión
        if not target_event:
            for ev in events:
                blob = f"{ev.get('summary', '')} {ev.get('description', '')}".lower()
                if ev.get("hangoutLink") or any(k in blob for k in ["asesor", "reuni", "llamada", "demo", "sdr", "nodepath"]):
                    target_event = ev
                    break

        if not target_event:
            return {"synced": False, "message": "No se encontró un evento coincidente para el lead"}

        event_id = target_event.get("id")
        existing_attendees = target_event.get("attendees", []) or []
        already_invited = any((a.get("email") or "").strip().lower() == clean_kam_email for a in existing_attendees)

        updated_attendees = list(existing_attendees)
        if not already_invited:
            updated_attendees.append({"email": clean_kam_email})

        patch_payload = {
            "attendees": updated_attendees,
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 60},
                    {"method": "popup", "minutes": 15},
                ],
            },
        }
        patch_url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"
        patch_resp = requests.patch(patch_url, headers=headers, params={"sendUpdates": "all"}, json=patch_payload, timeout=15)
        if patch_resp.status_code not in [200, 201]:
            return {"synced": False, "message": f"Error actualizando evento ({patch_resp.status_code}): {patch_resp.text}"}

        ev_data = patch_resp.json()
        return {
            "synced": True,
            "event_id": event_id,
            "summary": ev_data.get("summary", ""),
            "start": (ev_data.get("start") or {}).get("dateTime") or (ev_data.get("start") or {}).get("date", ""),
            "meet_link": ev_data.get("hangoutLink", ""),
            "already_invited": already_invited,
            "message": "KAM sincronizado en Google Calendar con recordatorios pre-reunión",
        }
    except Exception as e:
        logger.error(f"Error syncing KAM to calendar: {e}")
        return {"synced": False, "message": str(e)}


def get_upcoming_meetings_window(access_token: str, refresh_token: str, min_minutes: int = 45, max_minutes: int = 75) -> list:
    """
    Obtiene los eventos de Google Calendar que comienzan dentro de la ventana [min_minutes, max_minutes]
    desde ahora, para disparar recordatorios automáticos por WhatsApp.
    """
    if not access_token:
        return []
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        time_min = (now_utc + datetime.timedelta(minutes=min_minutes)).isoformat()
        time_max = (now_utc + datetime.timedelta(minutes=max_minutes)).isoformat()

        list_url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 20,
        }
        resp = requests.get(list_url, headers=headers, params=params, timeout=15)
        if resp.status_code == 401 and refresh_token:
            new_token = refresh_google_token(refresh_token)
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                resp = requests.get(list_url, headers=headers, params=params, timeout=15)

        if resp.status_code != 200:
            return []

        results = []
        for ev in resp.json().get("items", []):
            if ev.get("status") == "cancelled":
                continue
            start_dt = (ev.get("start") or {}).get("dateTime")
            if not start_dt:
                continue
            results.append({
                "event_id": ev.get("id", ""),
                "summary": ev.get("summary", ""),
                "description": ev.get("description", ""),
                "start_time": start_dt,
                "meet_link": ev.get("hangoutLink", "") or ev.get("htmlLink", ""),
                "attendees": [a.get("email", "") for a in (ev.get("attendees") or []) if a.get("email")],
            })
        return results
    except Exception as e:
        logger.error(f"Error fetching upcoming meetings window: {e}")
        return []
