# Copyright (c) 2026, Faris Ansari and contributors
# For license information, please see license.txt

"""The link preview a chat app or social site draws for /u/<username>/<slug>.

A crawler is a Guest that runs no script, so everything it reads must be in
the served <head>: the title, a description, and the light Thumbnail as the
image. A private Prototype names no image, because a Guest cannot read it.
"""

from frappe.tests import IntegrationTestCase, set_user

from sketch import prototype, prototype_files, thumbnails
from sketch.tests import utils
from sketch.viewer import SketchViewerRenderer

FILES = {"src/App.vue": "<template><h1>hello</h1></template>\n"}

PIXEL_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


class TestViewerLinkPreview(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		utils.require_runtime()
		cls.user = utils.make_user("preview", "d2tpreview")
		cls.addClassCleanup(utils.drop_user, cls.user)
		cls.username = utils.username_of(cls.user)

	def make(self, slug: str, is_public: bool, title: str = "Leave Tracker"):
		doc = utils.make_prototype(self.user, slug, files=FILES, is_public=is_public, title=title)
		self.addCleanup(utils.drop_prototype, doc.name)
		self.addCleanup(thumbnails.forget, doc.name)
		shot = {"theme": "light", "route": "/", "png_base64": PIXEL_B64}
		thumbnails.store(doc.name, [shot], prototype_files.revision(doc.name))
		return doc

	def head(self, doc) -> str:
		with set_user("Guest") if doc.is_public else set_user(self.user):
			renderer = SketchViewerRenderer(path=f"u/{self.username}/{doc.slug}")
			self.assertTrue(renderer.can_render())
			html = renderer.render().get_data(as_text=True)

		return html.split("</head>", 1)[0]

	def test_a_public_link_shows_title_description_and_screenshot(self):
		doc = self.make("d2t-preview-public", is_public=True)
		head = self.head(doc)

		self.assertIn("<title>Leave Tracker</title>", head)
		self.assertNotIn("<title>Prototype</title>", head)
		self.assertIn('<meta property="og:title" content="Leave Tracker" />', head)
		self.assertIn('<meta property="og:description" content="A prototype by ', head)
		self.assertIn('<meta name="twitter:card" content="summary_large_image" />', head)

		image = (
			f"{prototype.site_url()}/t/{self.username}/{doc.slug}/light.png?rev={thumbnails.stamp(doc.name)}"
		)
		self.assertIn(f'<meta property="og:image" content="{image}" />', head)
		self.assertIn(f'<meta property="og:url" content="{prototype.public_url(doc)}" />', head)

	def test_a_private_link_names_no_image(self):
		doc = self.make("d2t-preview-private", is_public=False)
		head = self.head(doc)

		self.assertIn("<title>Leave Tracker</title>", head)
		self.assertNotIn("og:image", head)
		self.assertIn('<meta name="twitter:card" content="summary" />', head)

	def test_the_title_is_escaped(self):
		doc = self.make("d2t-preview-escape", is_public=True, title='Q&A "draft"')
		head = self.head(doc)

		self.assertIn("<title>Q&amp;A &quot;draft&quot;</title>", head)
		self.assertIn('<meta property="og:title" content="Q&amp;A &quot;draft&quot;" />', head)
