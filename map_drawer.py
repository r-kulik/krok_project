import folium
import branca
import datetime

import geopandas as gpd
import pandas as pd
from celery import Celery
from flask import Flask, request, jsonify, url_for, render_template
from shapely.geometry import *

from run import main as get_data

app = Flask(__name__)
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

# Loading GeoJson with MSK districts into gdf.
fr = "mo.geojson"
geo = gpd.read_file(fr)
print("---------- / GDF is ready. / ----------")


# for _, row in rdf.iterrows():
#     point: Point = row["geometry"]
#     for _, r2 in gdf.iterrows():
#         if point.within(r2.geometry):
#             if poly_data.get(r2.NAME):
#                 if poly_data[r2.NAME][1]:
#                     poly_data[r2.NAME][1] += 1
#             else:
#                 poly_data[r2.NAME] = []
#                 poly_data[r2.NAME].append(r2.NAME)
#                 poly_data[r2.NAME].append(1)
#             break

@celery.task(bind=True)
def get_gpdata(self, tag="кофе", radius=25000, since=datetime.datetime(2010, 1, 1).timestamp()):
    tot = len(geo)
    self.update_state(state='PROGRESS',
                      meta={'current': 0, 'total': tot,
                            'status': "Парсим посты ВК... "})
    pdf = pd.DataFrame.from_records(get_data(tag, radius, since))
    try:
        pdf.long
    except AttributeError:
        return {'current': tot, 'total': tot, 'status': 'Ключевое слово не обнаружено.',
                'result': ''}
    rdf = gpd.GeoDataFrame(pdf, geometry=gpd.points_from_xy(pdf.long, pdf.lat))

    self.update_state(state='PROGRESS',
                      meta={'current': 0, 'total': tot,
                            'status': "Загрузка данных из ВК завершена. "})

    poly_data = []
    for z, r2 in geo.iterrows():
        num = 0
        for k, row in rdf.iterrows():
            point: Point = row.geometry
            if point.within(r2.geometry):
                num += 1
                rdf.drop(k, inplace=True)
        poly_data.append([r2.NAME, num])
        self.update_state(state='PROGRESS',
                          meta={'current': z + 1, 'total': tot,
                                'status': "Обработано районов: "})

    return {'current': tot, 'total': tot, 'status': 'Обработка завершена! ',
            'result': poly_data}


@app.route('/map/kick')
def kick_map():
    task = get_gpdata.apply_async(kwargs=request.args)
    return jsonify({"redirect": url_for('taskstatus',
                                        task_id=task.id)}), 202, {'Location': url_for('taskstatus',
                                                                                      task_id=task.id)}


@app.route('/map/status/<task_id>')
def taskstatus(task_id):
    task = get_gpdata.AsyncResult(task_id)
    if task.state == 'PENDING':
        # job did not start yet
        response = {
            'state': task.state,
            'current': 0,
            'total': 1,
            'status': ' Ожидайте...'
        }
    elif task.state != 'FAILURE':
        response = {
            'state': task.state,
            'current': task.info.get('current', 0),
            'total': task.info.get('total', 1),
            'status': task.info.get('status', '')
        }
        if 'result' in task.info:
            response['ready_url'] = url_for('i_map', task_id=task_id)
    else:
        # something went wrong in the background job
        response = {
            'state': task.state,
            'current': 1,
            'total': 1,
            'status': str(task.info),  # this is the exception raised
        }
    return jsonify(response)


@app.route("/map/<task_id>")
def i_map(task_id):
    task = get_gpdata.AsyncResult(task_id)
    poly_data = pd.DataFrame(task.info["result"], columns=["district", "num"])
    # Convert the GeoDataFrame to WGS84 coordinates
    colorscale = branca.colormap.linear.YlOrRd_09.scale(0, 50e3)

    def style_function(feature):
        return {
            'fillOpacity': 0.5,
            'weight': 0,
            'fillColor': colorscale
        }

    map_data = folium.features.Choropleth(geo_data=geo, data=poly_data, columns=["district", "num"],
                                          key_on="feature.properties.NAME",
                                          fill_color='YlOrBr',
                                          fill_opacity=0.6,
                                          legend_name='Кол-во результатов по запросу (точек): ',
                                          highlight=True)

    # Initializing Folium map
    mmap = folium.Map((55.75, 37.61), zoom_start=10)

    map_data.layer_name = "Данные"
    mmap.add_child(map_data)
    folium.LayerControl().add_to(mmap)
    # folium.plugins.Fullscreen().add_to(mmap)

    return mmap._repr_html_()


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == '__main__':
    app.run()
