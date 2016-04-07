#!/usr/bin/env python
#
# Copyright 2012 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

""" Contains the Datastore model classes used by the app"""

import logging

import categories
import docs

from google.appengine.api import memcache
from google.appengine.ext import ndb

class Category(ndb.Model):
  """The model class for product category information.  Supports building a
  category tree."""

  _CATEGORY_INFO = None
  _CATEGORY_DICT = None
  _RCATEGORY_DICT = None
  _ROOT = 'root'  # the 'root' category of the category tree

  parent_category = ndb.KeyProperty()

  @property
  def category_name(self):
    return self.key.id()

  @classmethod
  def deleteCategories(cls):
    logging.info("deleteCat()")
    cats = cls.query().fetch()
    [c.key.delete() for c in cats]
    cls._CATEGORY_INFO = None
    cls._CATEGORY_DICT = None
    cls._RCATEGORY_DICT = None

  @classmethod
  def buildAllCategories(cls):
    """ build the category instances from the provided static data if they
        don't exist."""
    logging.info("buildCat()")
    # Don't build if there are any categories in the datastore already
    if cls.query().get():
      return
    root_category = categories.ctree
    cls.buildCategory(root_category, None)

  @classmethod
  def buildCategory(cls, category_data, parent_key):
    """build a category and any children from the given data dict."""

    if not category_data:
      return
    cname = category_data.get('name')
    if not cname:
      logging.warn('no category name for %s', categories)
      return
    if parent_key:
      cat = cls(id=cname, parent_category=parent_key)
    else:
      cat = cls(id=cname)
    cat.put()

    children = category_data.get('children')
    # if there are any children, build them using their parent key
    cls.buildChildCategories(children, cat.key)

  @classmethod
  def buildChildCategories(cls, children, parent_key):
    for cat in children:
      cls.buildCategory(cat, parent_key)

  @classmethod
  def getCategoryInfo(cls):
    """Build and cache a list of category id/name correspondences.  This info is
    used to populate html select menus."""
    if not cls._CATEGORY_INFO:
      cls.buildAllCategories()  #first build categories from data file
          # if required
      cats = cls.query().fetch()
      cls._CATEGORY_INFO = [(c.key.id(), c.key.id()) for c in cats
            if c.key.id() != cls._ROOT]
    return cls._CATEGORY_INFO

class Product(ndb.Model):
  """Model for Product data."""

  doc_id = ndb.StringProperty()  # the id of the associated product
  user_id = ndb.IntegerProperty()
  location = ndb.StringProperty()
  price = ndb.FloatProperty()
  ppacc = ndb.StringProperty()
  category = ndb.StringProperty()
  # average rating of the product over all its reviews
  avg_rating = ndb.FloatProperty(default=0)
  # the number of reviews of that product
  num_reviews = ndb.IntegerProperty(default=0)
  image_url = ndb.StringProperty(default="http:///")
  phone_number = ndb.StringProperty()
  adress = ndb.StringProperty()
  active = ndb.BooleanProperty(default=True)
  start_date = ndb.StringProperty()
  end_date = ndb.StringProperty()
  # indicates whether the associated document needs to be re-indexed due to a
  # change in the average review rating.
  needs_review_reindex = ndb.BooleanProperty(default=False)
  
  @property
  def pid(self):
    return self.key.id()

  def reviews(self):
    return Review.query(
        Review.active == True,
        Review.rating_added == True,
        Review.product_key == self.key).fetch()

  @classmethod
  def updateProdDocsWithNewRating(cls, pkeys):
    """ Add new rating to a document"""

    doclist = []

    def _tx(pid):
      prod = cls.get_by_id(pid)
      if prod and prod.needs_review_reindex:

        # update the associated document with the new ratings info
        # and reindex
        modified_doc = docs.Product.updateRatingInDoc(
            prod.doc_id, prod.avg_rating)
        if modified_doc:
          doclist.append(modified_doc)
        prod.needs_review_reindex = False
        prod.put()
    for pkey in pkeys:
      ndb.transaction(lambda: _tx(pkey.id()))
    # reindex all modified docs in batch
    docs.Product.add(doclist)

  @classmethod
  def create(cls, params, doc_id):
    """Create a new product """
    prod = cls(
        id=params['pid'], price=params['price'],
        category=params['category'], doc_id=doc_id)
    prod.put()
    return prod

  def update_core(self, params, doc_id):
    """Update 'core' values from the given params dict and doc_id."""
    self.populate(
        price=params['price'], category=params['category'],
        doc_id=doc_id)

  @classmethod
  def updateProdDocWithNewRating(cls, pid):
    """Given the id of a product entity, see if it is marked as needing
    a document re-index.  This flag is set when a new review is created for
    that product.  If it needs a re-index, call the document method."""

    def _tx():
      prod = cls.get_by_id(pid)
      if prod and prod.needs_review_reindex:
        prod.needs_review_reindex = False
        prod.put()
      return (prod.doc_id, prod.avg_rating)
    (doc_id, avg_rating) = ndb.transaction(_tx)
    # update the associated document with the new ratings info
    # and reindex
    docs.Product.updateRatingsInfo(doc_id, avg_rating)

class Review(ndb.Model):
  """Model for Review data. Associated with a product entity via the product
  key."""

  doc_id = ndb.StringProperty()
  date_added = ndb.DateTimeProperty(auto_now_add=True)
  product_key = ndb.KeyProperty(kind=Product)
  username = ndb.StringProperty()
  rating = ndb.IntegerProperty()
  active = ndb.BooleanProperty(default=True)
  comment = ndb.TextProperty()
  rating_added = ndb.BooleanProperty(default=False)

  @classmethod
  def deleteReviews(cls, pid):
    """Deletes the reviews associated with a product id."""
    if not pid:
      return
    reviews = cls.query(
        cls.product_key == ndb.Key(Product, pid)).fetch(keys_only=True)
    return ndb.delete_multi(reviews)

class UserInfo(ndb.Model):
  # Keyed by user_id
  nickname = ndb.StringProperty()
  email = ndb.StringProperty()
  phoneNumber = ndb.StringProperty()
  meetPoint = ndb.StringProperty()


class Transaction(ndb.Model):
    # Keyed by user_id
    t_id = ndb.StringProperty()  # Transaction ID
    doc_id = ndb.StringProperty()
    product = ndb.StringProperty()
    rentee_id = ndb.StringProperty()
    renter_id = ndb.StringProperty()
    email = ndb.StringProperty()
    phone_number = ndb.StringProperty()
    meet_point = ndb.StringProperty()
    pickupD = ndb.DateProperty()
    returnD = ndb.DateProperty()
    amount_paid = ndb.FloatProperty()
    dateSent = ndb.DateTimeProperty(auto_now_add=True)
    payment_status = ndb.StringProperty()
    custom = ndb.StringProperty()
    verified = ndb.BooleanProperty()

    @classmethod
    def get_by_doc_id(cls, user_id):
        q1 = cls.query()
        return q1.filter(cls.doc_id == user_id)

    @classmethod
    def deleteTransactions(cls):
        logging.info("deleteCat()")
        cats = cls.query().fetch()
        [c.key.delete() for c in cats]
