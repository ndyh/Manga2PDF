import os
import re
import uuid
import json
import boto3
import shutil
import requests
from PIL import Image
from fpdf import FPDF
from bs4 import BeautifulSoup
from botocore.client import Config

# RESPONSE_HEADERS - To circumvent triggering CORS
RESPONSE_HEADERS = {
    'Access-Control-Request-Headers': 'Content-Type,Access-Control-Allow-Headers,Access-Control-Allow-Origin', 
    'Content-Type' : 'application/json',
    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
    'Access-Control-Allow-Origin': '*'
}

# SESSION_HEADERS - Acceptable headers for pulling images to write to tmp
SESSION_HEADERS = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'referer': 'https://readmanganato.com',
}

# list - list to alphanumerically sort and return
def alphanum_sort(list):
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(list, key=alphanum_key)

# url - url to parse through beautifulsoup
def parser(url):
    return BeautifulSoup(requests.get(url).content, 'html.parser')

# html - parsed html including user query to pull list of relevant manga    
def pull_story_list(html):
    story_list = {}
    container = html.find('div', {'class': 'panel-search-story'})
    for item in container.find_all('div', {'class': 'search-story-item'}):
        for data in item.find_all('a', {'class': 'item-img'}):
            title, img_src = '', ''
            link = data['href']
            for img in data.find_all('img'):
                title = img['alt']
                img_src = img['src']
            story_list[link] = {
                'title': title,
                'thumbnail': img_src,
                'chapters': []
            }
    return story_list

# html - parsed html to pull manga info from (gengres, description, total chapters)    
def pull_story_info(html):
    counter = 0
    genres = []
    for g in (html.find('i', {'class': 'info-genres'}).parent.parent).find('td', {'class': 'table-value'}).find_all('a', {'class': 'a-h'}):
        genres.append(g.text)
    desc = html.find('div', {'id': 'panel-story-info-description'}).text
    chapter_container = html.find('ul', {'class': 'row-content-chapter'})
    for c in chapter_container.find_all('li', {'class': 'a-h'}):
        counter = counter + 1
    info = {
        'genres': genres,
        'desc': desc[15:],
        'chapters': counter
    }
    return info

# html - parsed html from manga series chapter to pull images from
# chapter_dir - directory to save images to
def pull_chapter_images(html, chapter_dir):
    chapter_image_container = html.find('div', {'class': 'container-chapter-reader'})
    for idx, img in enumerate(chapter_image_container.find_all('img')):
        src = img.get('src')
        img_filepath = f'{chapter_dir}{str(idx+1)}.jpg'
        with open(img_filepath, 'wb') as file:
            session = requests.Session()
            response = session.get(src, stream=False, headers=SESSION_HEADERS)
            if not response.ok:
                return response
            for block in response.iter_content(1024):
                if not block:
                    break
                file.write(block)

# pdf - pdf to add images to
# chapter_dir - directory to pull images from
# img - image to add to pdf
def add_page_to_pdf(pdf, chapter_dir, img):
    pdf_sizes = {
        'Portrait': {'w': 210, 'h': 297},
        'Landscape': {'w': 297, 'h': 210}
    }
    i = Image.open(f'{chapter_dir}/{img}')
    w, h = i.size
    w, h = float(w * 0.264583), float(h * 0.264583)
    orientation = 'Portrait' if w < h else 'Landscape'
    w = w if w < pdf_sizes[orientation]['w'] else pdf_sizes[orientation]['w']
    h = h if h < pdf_sizes[orientation]['h'] else pdf_sizes[orientation]['h']
    pdf.add_page(orientation=orientation)
    try:
        pdf.image(f'{chapter_dir}/{img}', 0, 0, w, h)
    except:
        try:
            i.save(f'{chapter_dir}/{img[:img.find(".jpg")]}.png')
            pdf.image(f'{chapter_dir}/{img[:img.find(".jpg")]}.png', 0, 0, w, h)
        except:
            return 'Failure to convert'

# file - file to upload to s3 bucket and return dl link to
def upload_to_s3(file):
    bucket = 'manga2pdf'
    print(f'Uploading {file} to S3')
    s3 = boto3.client('s3', config=Config(connect_timeout=420))
    s3.upload_file(file, bucket, file[5:])
    # ExtraArgs={'ACL':'public-read'}
    link = s3.generate_presigned_url(
        'get_object', 
        ExpiresIn=5000, 
        Params={'Bucket': bucket, 'Key': file[5:]}
    )
    print(link)
    return link

# series_id - manga series id
# directory - directory to create and write to
# file_name - name to be used during pdf creation and uploading
# c_min - first chapter to pull
# c_max - last chapter to pull
def create_and_upload(series_id, directory, file_name, c_min, c_max):
    os.mkdir(directory)
    pdf = FPDF() 
    threads = []
    for chapter in range(int(c_min), (int(c_max) + 1)):
        chapter_dir = f'{directory}/{str(chapter)}/'
        os.mkdir(chapter_dir)
        pull_chapter_images(
            parser(f'https://readmanganato.com/manga-{series_id}/chapter-{str(chapter)}'), 
            chapter_dir
        )
        for img in alphanum_sort(os.listdir(chapter_dir)):
            add_page_to_pdf(pdf, chapter_dir, img)
    pdf.output(file_name, 'F')
    link = upload_to_s3(file_name)
    shutil.rmtree(directory)
    print(f'{directory} removed from /tmp/')
    return link

def lambda_handler(event, context):
    if event['path'] == '/s':
        keyword = event['queryStringParameters']['q']
        story_info = pull_story_list(parser(f'https://manganato.com/search/story/{keyword}'))
        return {
            'statusCode': 200,
            'headers': RESPONSE_HEADERS,
            'body': json.dumps(story_info)
        }
    elif event['path'] == '/f':
        story = pull_story_info(parser(event['queryStringParameters']['s']))
        return {
            'statusCode': 200,
            'headers': RESPONSE_HEADERS,
            'body': json.dumps(story)
        }
    elif event['path'] == '/c':
        series_id = event['queryStringParameters']['s'][len(event['queryStringParameters']['s']) - 8:]
        chapter_min = event['queryStringParameters']['f']
        chapter_max = event['queryStringParameters']['l']
        directory = f'/tmp/{uuid.uuid4()}_{series_id}'
        file_name = f'{directory}/{series_id}_{str(chapter_min)}-{str(chapter_max)}.pdf'

        link = create_and_upload(series_id, directory, file_name, chapter_min, chapter_max)

        return {
            'statusCode': 200,
            'headers': RESPONSE_HEADERS,
            'body': json.dumps(link)
        }