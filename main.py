from datetime import datetime
import certifi
import paho.mqtt.client as mqtt
import json
from dotenv import load_dotenv
import os
from sqlalchemy import text
from dw import get_dw
from insert_sensor_metadata import insert_sensor_metadata
from lists import *

# Ennen MQTT Brokerista vastaanotettavien viestien käsittelemistä ja
# lisäämistä lisätään tietueet sensors_dim-tauluun.
insert_sensor_metadata()

# MQTT-VIESTIN KÄSITTELY ######################################################

# Kerrotaan tiedosto, josta salaiset ympäristömuuttujat haetaan:
load_dotenv(dotenv_path=".env")

# Haetaan jokainen ympäristömuuttuja omaan muuttujaansa:
topic = os.environ.get("TOPIC")
username = os.environ.get("UN")
password = os.environ.get("PW")
host = os.environ.get("HOST")

# Jos autorisointiongelmia ilmenee, tarkistetaan että ympäristömuuttujista
# on haettu oikeat arvot:
print(topic)
print(username)
print(password)
print(host + "\n")


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected with result code {reason_code}, date: {datetime.now()}")
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    # Topic, johon julkaisut tulevat:
    client.subscribe(topic)


# EI KÄYTÖSSÄ (vanha _get_date_key() käytti tätä)
def _get_dates_dim(_dw):
    _query = text("SELECT * FROM dates_dim;")
    rows = _dw.execute(_query).mappings().all()
    return rows


def _get_sensors_dim(_dw):
    _query = text("SELECT sensor_id, device_id, sensor_key FROM sensors_dim;")
    rows = _dw.execute(_query).mappings().all()
    return rows


# VANHA date_key getter
def _get_date_key_old(msg_dt, dates):
    for d_dim in dates:
        if msg_dt.year == d_dim["year"] and msg_dt.month == d_dim["month"] and msg_dt.isocalendar().week == d_dim[
            "week"] and msg_dt.day == d_dim["day"] and msg_dt.hour == d_dim["hour"] and msg_dt.minute == d_dim[
            "min"] and msg_dt.second == d_dim["sec"] and msg_dt.microsecond == d_dim["ms"]:
            return d_dim["date_key"]
    return None


# UUSI date_key getter
def _get_date_key(_dw, msg_dt):
    _query = text("SELECT date_key FROM dates_dim d "
                  "WHERE d.year = :year AND d.month = :month AND d.day = :day "
                  "AND d.hour = :hour AND d.min = :min AND d.sec = :sec AND d.ms = :ms;")

    rows = _dw.execute(
        _query,
        {
            "year": msg_dt.year,
            "month": msg_dt.month,
            "day": msg_dt.day,
            "hour": msg_dt.hour,
            "min": msg_dt.minute,
            "sec": msg_dt.second,
            "ms": msg_dt.microsecond
        }
    ).mappings().all()

    if rows:
        return rows[0]['date_key']

    return None


def _get_sensor_key(msg_sensor_id, msg_device_id, sensors_from_dw):
    for sensor in sensors_from_dw:
        if msg_sensor_id == sensor["sensor_id"] and msg_device_id == sensor["device_id"]:
            return sensor["sensor_key"]
    return None


# Alustetaan dictionary, jota hyödynnetään sensoreiden kumulatiivisten
# arvojen käsittelyssä:
consumptions_and_productions = {
    # Lights
    "68_50_1_Value_65537": None,
    "68_50_2_Value_65537": None,
    "71_50_1_Value_65537": None,
    "71_50_2_Value_65537": None,
    "103_50_1_Value_65537": None,
    "106_50_1_Value_65537": None,
    "110_50_1_Value_65537": None,
    "112_50_1_Value_65537": None,
    "116_50_1_Value_65537": None,
    "120_50_1_Value_65537": None,
    "122_50_1_Value_65537": None,
    "122_50_2_Value_65537": None,
    "141_50_1_Value_65537": None,
    "141_50_2_Value_65537": None,
    "142_50_1_Value_65537": None,
    "142_50_2_Value_65537": None,
    # Outlets
    "53_50_0_Value_65537": None,
    "54_50_0_Value_65537": None,
    "148_50_0_Value_65537": None,
    "150_50_0_Value_65537": None,
    "151_50_0_Value_65537": None,
    "152_50_0_Value_65537": None,
    "153_50_0_Value_65537": None,
    "154_50_0_Value_65537": None,
    "155_50_0_Value_65537": None,
    "156_50_0_Value_65537": None,
    "157_50_0_Value_65537": None,
    "158_50_0_Value_65537": None,
    "159_50_0_Value_65537": None,
    "161_50_0_Value_65537": None,
    "162_50_0_Value_65537": None,
    "163_50_0_Value_65537": None,
    "164_50_0_Value_65537": None,
    "166_50_0_Value_65537": None,
    "167_50_0_Value_65537": None,
    "168_50_0_Value_65537": None,
    "170_50_0_Value_65537": None,
    "171_50_0_Value_65537": None,
    "172_50_0_Value_65537": None,
    "173_50_0_Value_65537": None,
    "175_50_0_Value_65537": None,
    "176_50_0_Value_65537": None,
    "177_50_0_Value_65537": None,
    # Heating
    "47_50_1_Value_65537": None,
    "47_50_2_Value_65537": None,
    # Production
    "produced_energy": None,
    "yieldtoday": None,
    # Total Consumption
    "189_50_1_Value_65537": None,
    "energy": None
}


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    try:
        with get_dw() as _dw:
            try:
                # Muutetaan yksittäisen viestin tietosisältö dictionary-muotoon
                payload = json.loads(msg.payload)

                # Muutetaan aikaleima/epoch luettavaan päivämäärämuotoon. Koska
                # muunnoksessa käytetään datetimen fromtimestamp-funktiota, on
                # aikaleima muutettava ensin millisekunneista sekunneiksi. Koska
                # tietokannassa on sarake myös mikrosekunneille, ei pyöristetä
                # yksikkömuunnoksen osamäärää (ei käytetä Python integer
                # divisionia).
                ts_in_sec = payload['ts'] / 1000

                # Muunnetaan sekuntimuotoinen epoch päivämääräksi.
                dt = datetime.fromtimestamp(ts_in_sec)

                # Alustetaan query, joka toteutetaan myöhemmin ehtolauseiden
                # perusteella:
                _dates_dim_query = text(
                    'INSERT INTO dates_dim (year, month, week, day, hour, min, sec, ms) VALUES ('
                    ':year, :month, :week, :day, :hour, :min, :sec, :ms)')

                # Haetaan tietosisällöstä laitteen nimi/id hakemalla
                # viesti-dictionaryn d-avaimen arvona olevan dictionaryn avaimen
                # nimi. Koska keys-funktio palauttaa haetun arvon objektin sisällä
                # olevaan listaan, muutetaan tulos tupleksi ja haetaan avaimen nimi
                # tuplen ainoasta eli ensimmäisestä alkiosta.
                device_id_msg = tuple(payload['d'].keys())[0]

                # Haetaan tietosisällöstä tiedot laitteen sensoreista:
                sensor_data = payload['d'][device_id_msg]

                # Haetaan laitteen sensoreiden nimet/tunnisteet:
                sensor_ids_msg = list(tuple(sensor_data.keys()))

                # Koska laitteissa voi olla useampia sensoreita, haetaan laitteen
                # sensoreiden arvot silmukassa. Lisätään samalla kunkin
                # sensorin tiedot tietokantaan.
                for sensor_id_msg in sensor_ids_msg:
                    sensor_value = sensor_data[sensor_id_msg]['v']
                    #dates_dim = _get_dates_dim(_dw)                # Poistettu käytöstä
                    sensors_dim = _get_sensors_dim(_dw)
                    _sensor_key = _get_sensor_key(sensor_id_msg, device_id_msg, sensors_dim)
                    _date_key = None

                    # Jos viestin sensori kuuluu kulutusta ja tuottoa
                    # mittaavien sensoreiden dictionaryyn ja jos sillä ei ole
                    # dictionaryssa arvoa, lisätään arvo. Jos arvo on,
                    # asetetaan sensorin epäkumulatiivista arvoa kuvaavan
                    # muuttujan arvoksi nykyisessä ja edellisessä viestissä
                    # tulleiden arvojen erotus. Epäkumulatiivinen arvo
                    # lisätään ehtolauseiden määrittämään tauluun.
                    #
                    # Jos sensori ei kuulu kulutusta ja tuottoa mittaavien
                    # sensoreiden dictionaryyn, lisätään viestissä tullut
                    # arvo joko temperatures_fact tai
                    # measurements_fact-tauluun. Ennen arvon lisäämistä
                    # tauluun, lisätään sen aikaleima dates_dim-tauluun.
                    # Arvo lisätään tauluun vain jos jokin yllä mainituista
                    # erittelyehdoista toteutuu ja jos sille on olemassa
                    # date_key ja sensor_key.

                    if sensor_id_msg in consumptions_and_productions:
                        if consumptions_and_productions[sensor_id_msg] is None:
                            consumptions_and_productions[sensor_id_msg] = sensor_value
                        else:
                            # Sensorin epäkumulatiivinen arvo saadaan
                            # laskemalla sen uuden ja edellisen arvon
                            # erotus.
                            noncumulative_sensor_value = sensor_value - consumptions_and_productions[sensor_id_msg]
                            # Asetetaan sensorin arvoksi dictionaryyn
                            # viestissä tullut uusi arvo.
                            consumptions_and_productions[sensor_id_msg] = sensor_value

                            # Jos epäkumulatiivinen arvo on negatiivinen,
                            # viestin tietoja ei lisätä tietokantaan.
                            # Negatiivinen arvo indikoi nollattua sensoria,
                            # jonka uusi epäkumulatiivinen arvo voidaan
                            # laskea sensorin seuraavasta viestistä, sillä
                            # 0-arvo on otettu ylemmällä rivillä talteen.
                            if noncumulative_sensor_value < 0:
                                continue

                            # Lisätään viestin aikaleima dates_dim-tauluun:
                            _dw.execute(_dates_dim_query,
                                        {'year': dt.year, 'month': dt.month, 'week': dt.isocalendar().week,
                                         'day': dt.day,
                                         'hour': dt.hour, 'min': dt.minute, 'sec': dt.second, 'ms': dt.microsecond})

                            # Haetaan viestin date_key dates_dim-taulusta:
                            _date_key = _get_date_key(_dw, dt)

                            # Jos sensorin id löytyy yhdestäkään listasta, jossa
                            # luetellaan kulutusta indikoivien sensorien id:t,
                            # lisätään value tauluun, joka kokoaa kaikkien
                            # kulutusta mittaavien sensoreiden kulutusarvot:
                            if (sensor_id_msg in lights_ids or sensor_id_msg in outlet_ids or sensor_id_msg in heater_ids) and _date_key is not None and _sensor_key is not None:
                                _total_consumptions_fact_query = text("INSERT INTO total_consumptions_fact ("
                                                                      "sensor_key, date_key, value) VALUES ("
                                                                      ":sensor_key, :date_key, :value)")
                                _dw.execute(_total_consumptions_fact_query,
                                            {"sensor_key": _sensor_key, "date_key": _date_key,
                                             "value": noncumulative_sensor_value})

                            # Jos sensorin id löytyy lights_id-listasta, lisätään
                            # value lighting_consumptions_fact-tauluun:
                            if (sensor_id_msg in lights_ids) and _date_key is not None and _sensor_key is not None:
                                _lighting_consumptions_fact_query = text("INSERT INTO lighting_consumptions_fact ("
                                                                         "sensor_key, date_key, value) VALUES ("
                                                                         ":sensor_key, :date_key, :value)")
                                _dw.execute(_lighting_consumptions_fact_query,
                                            {"sensor_key": _sensor_key, "date_key": _date_key,
                                             "value": noncumulative_sensor_value})

                            # Jos sensorin id löytyy outlet_ids-listasta, lisätään
                            # value outlets_consumptions_fact-tauluun:
                            elif sensor_id_msg in outlet_ids and _date_key is not None and _sensor_key is not None:
                                _outlets_consumptions_fact_query = text("INSERT INTO outlets_consumptions_fact ("
                                                                        "sensor_key, date_key, value) VALUES ("
                                                                        ":sensor_key, :date_key, :value)")
                                _dw.execute(_outlets_consumptions_fact_query,
                                            {"sensor_key": _sensor_key, "date_key": _date_key,
                                             "value": noncumulative_sensor_value})

                            # Jos sensorin id löytyy heater_id-listasta, lisätään
                            # value heating_consumptions_fact-tauluun:
                            elif sensor_id_msg in heater_ids and _date_key is not None and _sensor_key is not None:
                                _heating_consumptions_fact_query = text("INSERT INTO heating_consumptions_fact ("
                                                                        "sensor_key, date_key, value) VALUES ("
                                                                        ":sensor_key, :date_key, :value)")
                                _dw.execute(_heating_consumptions_fact_query,
                                            {"sensor_key": _sensor_key, "date_key": _date_key,
                                             "value": noncumulative_sensor_value})
                            # Jos sensorin id löytyy yhdestäkään listasta, jossa
                            # luetellaan tuottoa indikoivien sensorien id:t,
                            # lisätään value tauluun, joka kokoaa kaikkien
                            # tuottoa mittaavien sensoreiden tuottoarvot:
                            elif sensor_id_msg in total_production_ids and _date_key is not None and _sensor_key is not None:
                                _productions_fact_query = text("INSERT INTO productions_fact (sensor_key, date_key, "
                                                               "value) VALUES (:sensor_key, :date_key, :value)")
                                _dw.execute(_productions_fact_query,
                                            {"sensor_key": _sensor_key, "date_key": _date_key,
                                             "value": noncumulative_sensor_value})

                            # Total consumption voidaan laskea myös inverter + tb_3phasen consumptionien summasta.
                            # Laitetaan ne measurementsiin jos halutaankin käyttää sitä.
                            elif sensor_id_msg in total_consumption_ids and _date_key is not None and _sensor_key is not None:
                                _measurements_fact_query = text("INSERT INTO measurements_fact (sensor_key, date_key, "
                                                                "value) VALUES (:sensor_key, :date_key, :value)")
                                _dw.execute(_measurements_fact_query,
                                            {"sensor_key": _sensor_key, "date_key": _date_key,
                                             "value": noncumulative_sensor_value})

                    else:
                        # Jos sensorin id löytyy temperature_ids- tai
                        # battery_ids-listoista, lisätään viestin aikaleima
                        # dates_dim-tauluun ja haetaan saman tien viestin
                        # _date_key dates_dim-taulusta:
                        if sensor_id_msg in temperature_ids or sensor_id_msg in battery_ids:
                            _dw.execute(_dates_dim_query,
                                        {'year': dt.year, 'month': dt.month, 'week': dt.isocalendar().week,
                                         'day': dt.day,
                                         'hour': dt.hour, 'min': dt.minute, 'sec': dt.second, 'ms': dt.microsecond})

                            _date_key = _get_date_key(_dw, dt)

                        # Jos sensorin id löytyy temperatures listasta,
                        # sen arvo sijoitetaan temperatures_fact-tauluun,
                        # kunhan sille on olemassa _date_key ja _sensor_key:
                        if sensor_id_msg in temperature_ids and _date_key is not None and _sensor_key is not None:
                            _temperatures_fact_query = text("INSERT INTO temperatures_fact (sensor_key, date_key, "
                                                            "value) VALUES (:sensor_key, :date_key, :value)")
                            _dw.execute(_temperatures_fact_query,
                                        {"sensor_key": _sensor_key, "date_key": _date_key, "value": sensor_value})

                        # Laitetaan battery id:t measurementsiin
                        elif sensor_id_msg in battery_ids and _date_key is not None and _sensor_key is not None:
                            _measurement_fact_query = text("INSERT INTO measurements_fact (sensor_key, date_key, "
                                                           "value) VALUES (:sensor_key, :date_key, :value)")
                            _dw.execute(_measurement_fact_query,
                                        {"sensor_key": _sensor_key, "date_key": _date_key, "value": sensor_value})
                        else:
                            continue

                _dw.commit()

            except Exception as e1:
                print(e1)
                _dw.rollback()
                raise e1
    except Exception as e2:
        print(e2)


mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqttc.on_connect = on_connect
mqttc.on_message = on_message

# Käyttäjänimi ja salasana:
mqttc.username_pw_set(username, password)

# Koska käytetään suojattua yhteyttä (portti 8883), on kutsuttava
# tls_set-funktiota, jonka parametriksi on asetettava certifi-kirjaston
# where-funktiokutsu.
mqttc.tls_set(certifi.where())
# Määritellään viesteille host, portti ja ping-aika:
mqttc.connect(host, 8883, 60)

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
mqttc.loop_forever()
###############################################################################
