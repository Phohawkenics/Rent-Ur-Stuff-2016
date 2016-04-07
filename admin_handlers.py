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

""" Contains the admin request handlers for the app (those that require
 you to be logged in).
"""

import csv
import logging
import os
import urllib
import uuid
import webapp2

from base_handler import BaseHandler
import categories
import config
import docs
import errors
import models
import utils

from google.appengine.api import users
from google.appengine.ext.deferred import defer
from google.appengine.ext import ndb
from google.appengine.api import search
from google.appengine.api import urlfetch
from datetime import datetime


def deleteData(sample_data=True):

  # also reinstantiate categories list
  models.Category.deleteCategories()
  # delete all the product and review entities
  review_keys = models.Review.query().fetch(keys_only=True)
  ndb.delete_multi(review_keys)
  prod_keys = models.Product.query().fetch(keys_only=True)
  ndb.delete_multi(prod_keys)
  # delete all the associated product documents in the doc and
  # store indexes
  docs.Product.deleteAllInProductIndex()
  docs.Store.deleteAllInIndex()

class UserProfileHandler(BaseHandler):
  """Displays the user page."""

  def buildUserProfilePage(self, notification=None):
    user = users.get_current_user()
    userinfo = ndb.Key(models.UserInfo, user.user_id()).get()
    if userinfo is not None:
        userInfo = {
            'user_id': userinfo.key.id(),
            'nickname': userinfo.nickname,
            'email': userinfo.email,
            'phone_number': userinfo.phoneNumber,
            'meet_point': userinfo.meetPoint
        }
    else:
        userInfo = {
            'user_id': '',
            'nickname': user.nickname(),
            'email': user.email(),
            'phone_number': 'None',
            'meet_point': 'Montreal, Qc'
            }
    if notification:
      userInfo['notification'] = notification
    self.render_template('user_profile.html', userInfo)

  @BaseHandler.logged_in
  def get(self):
    self.buildUserProfilePage()

  def post(self):
    self.user_profile()

  def user_profile(self):
    user = users.get_current_user()
    userinfo = ndb.Key(models.UserInfo, user.user_id()).get()
    if userinfo is None:
        userinfo = models.UserInfo(
            id = users.get_current_user().user_id(),
            nickname = self.request.get('nickname'),
            email = self.request.get('email'),
            phoneNumber = self.request.get('phone_number'),
            meetPoint = self.request.get('meet_point')
        )
        userinfo.put()
        Notification = "Creation succesfully"
    else:
        userinfo.nickname = self.request.get('nickname')
        userinfo.email = self.request.get('email')
        userinfo.phoneNumber = self.request.get('phone_number')
        userinfo.meetPoint = self.request.get('meet_point')
        userinfo.put()
        Notification = "Updated succesfully"

    self.buildUserProfilePage(notification=Notification)


class AdminHandler(BaseHandler):
  """Displays the admin page."""

  def buildAdminPage(self, notification=None):
    # If necessary, build the app's product categories now.  This is done only
    # if there are no Category entities in the datastore.
    models.Category.buildAllCategories()
    tdict = {
        'sampleb': config.SAMPLE_DATA_BOOKS,
        'samplet': config.SAMPLE_DATA_TVS,
        'update_sample': config.DEMO_UPDATE_BOOKS_DATA}
    if notification:
      tdict['notification'] = notification
    self.render_template('admin.html', tdict)

  @BaseHandler.logged_in
  def get(self):
    action = self.request.get('action')
    if action == 'deleteData':
      # delete data
      defer(deleteData)
      self.buildAdminPage(notification="Delete performed.")
    else:
      self.buildAdminPage()

  def update_ratings(self):
    # get the pids of the products that need review info updated in their
    # associated documents.
    pkeys = models.Product.query(
        models.Product.needs_review_reindex == True).fetch(keys_only=True)
    # re-index these docs in batch
    models.Product.updateProdDocsWithNewRating(pkeys)


class DeleteProductHandler(BaseHandler):
  """Remove data for the product with the given pid, including that product's
  reviews and its associated indexed document."""

  @BaseHandler.logged_in
  def post(self):
    pid = self.request.get('pid')
    if not pid:  # this should not be reached
      msg = 'There was a problem: no product id given.'
      logging.error(msg)
      url = '/'
      linktext = 'Go to product search page.'
      self.render_template(
          'notification.html',
          {'title': 'Error', 'msg': msg,
           'goto_url': url, 'linktext': linktext})
      return

    # Delete the product entity within a transaction, and define transactional
    # tasks for deleting the product's reviews and its associated document.
    # These tasks will only be run if the transaction successfully commits.
    def _tx():
      prod = models.Product.get_by_id(pid)
      if prod:
        prod.key.delete()
        defer(models.Review.deleteReviews, prod.key.id(), _transactional=True)
        defer(
            docs.Product.removeProductDocByPid,
            prod.key.id(), _transactional=True)

    ndb.transaction(_tx)
    # indicate success
    msg = (
        'The product with product id %s has been ' +
        'successfully removed.') % (pid,)
    url = '/'
    linktext = 'Go to product search page.'
    self.render_template(
        'notification.html',
        {'title': 'Product Removed', 'msg': msg,
         'goto_url': url, 'linktext': linktext})


class CreateProductHandler(BaseHandler):
  """Handler to create a new product: this constitutes both a product entity
  and its associated indexed document."""

  def parseParams(self):
    """Filter the param set to the expected params."""
    pid = self.request.get('pid')
    doc = docs.Product.getDocFromPid(pid)
    params = {}
    if doc:  # populate default params from the doc
      fields = doc.fields
      for f in fields:
        params[f.name] = f.value
    else:
      # start with the 'core' fields
      params = {
          'pid': uuid.uuid4().hex,  # auto-generate default UID
          'name': '',
          'user_id': users.get_current_user().user_id(), #give id automatically to product
          'description': '',
          'category': '',
          'image_url': '',
          'price': '',
          'ppacc': '',
          'cat_info': models.Category.getCategoryInfo()
          }
      pf = categories.product_dict
      # add the fields specific to the categories
      for _, cdict in pf.iteritems():
        temp = {}
        for elt in cdict.keys():
          temp[elt] = ''
        params.update(temp)

    for k, v in params.iteritems():
      # Process the request params. Possibly replace default values.
      params[k] = self.request.get(k, v)
    return params

  @BaseHandler.logged_in
  def get(self):
    params = self.parseParams()
    self.render_template('create_product.html', params)

  @BaseHandler.logged_in
  def post(self):
    self.createProduct(self.parseParams())

  def createProduct(self, params):
    """Create a product entity and associated document from the given params
    dict."""

    try:
      product = docs.Product.buildProduct(params)
      self.redirect(
          '/product?' + urllib.urlencode(
              {'pid': product.pid, 'pname': params['name'],
               'category': product.category
              }))
    except errors.Error as e:
      logging.exception('Error:')
      params['error_message'] = e.error_message
      self.render_template('create_product.html', params)


class ViewTransactionsHandler(BaseHandler):
        def buildViewTransactionsPage(self, notification=None):
            stuff = models.Transaction.get_by_doc_id(users.get_current_user().user_id())
            logging.info(stuff)
            if not stuff.get():
                stuff = None
            transactions = {
                'transactions': stuff
            }
            if notification:
                transactions['notification'] = notification

            self.render_template('view_transactions.html', transactions)

        @BaseHandler.logged_in
        def get(self):
            self.buildViewTransactionsPage()

        @BaseHandler.logged_in
        def post(self):
            action = self.request.get('action')
            if action == 'add':
                transaction = models.Transaction()
                transaction.doc_id = users.get_current_user().user_id()
                transaction.product = 'Product XXXXXXX'
                transaction.rentee_id = 'John Carter'
                transaction.email = 'johncarter@mail.com'
                transaction.phone_number = '514-123-4567'
                transaction.meet_point = '1111 Barclay # 1'
                transaction.pick_up_date = '20/4/2016'
                transaction.return_date = '20/4/2016'
                transaction.amount_paid = '90$'
                transaction.put()
                self.buildViewTransactionsPage(notification='Transaction Add')
            elif action == 'delete':
                models.Transaction.deleteTransactions()
                self.buildViewTransactionsPage(notification='Transaction Deleted')


ACCOUNT_EMAIL = "seller@example.com"
PP_URL = "https://www.paypal.com/cgi-bin/webscr"

# Set to false if ready for production, true if using PayPal's IPN simulator -
#  https://developer.paypal.com/webapps/developer/applications/ipn_simulator
usePayPalSandbox = True

if usePayPalSandbox:
    # Do not change these values
    ACCOUNT_EMAIL = "s01@test.com"
    PP_URL = "https://www.sandbox.paypal.com/cgi-bin/webscr"

    # class IPNHandler(webapp2.RequestHandler):


class IPNHandler(BaseHandler):
    def post(self):
        parameters = None
        if self.request.POST:
            parameters = self.request.POST.copy()
        if self.request.GET:
            parameters = self.request.GET.copy()

        if parameters:
            # Send the notification back to Paypal for verification
            parameters['cmd'] = '_notify-validate'
            params = urllib.urlencode(parameters)
            status = urlfetch.fetch(
                url=PP_URL,
                method=urlfetch.POST,
                payload=params,
            ).content

        payment = models.Transaction(receiver_email=parameters['receiver_email'],
                                     transaction_id=parameters['txn_id'],
                                     transaction_type=parameters['txn_type'],
                                     payment_type=parameters['payment_type'],
                                     payment_status=parameters['payment_status'],
                                     amount=parameters['mc_gross'],
                                     currency=parameters['mc_currency'],
                                     payer_email=parameters['payer_email'],
                                     first_name=parameters['first_name'],
                                     last_name=parameters['last_name'],
                                     verified=False)

        # Get and store 'custom' field, if it exists.
        for item in parameters.items():
            if item[0] == 'custom':
                payment.custom = item[1]

        # Insert new transactions in the database.
        if models.Transaction.transaction_exists(payment.transaction_id, payment.payment_status):
            # This transaction has already been verified and processed.
            logging.debug('Transaction already exists')

        # Verify that the payment is confirmed by PayPal and that it is going to the correct account
        elif status == "VERIFIED" and payment.receiver_email == ACCOUNT_EMAIL:

            if payment.payment_status == "Completed":
                payment.verified = True
                # Insert actions to take if a completed transaction is received here:

            else:
                payment.verified = False
                # Insert actions to take if a transaction with unverified payment is received here:

            # Insert new (verified) transactions in the database. You may wish to store/log unverified transactions as well as these may be malicious.
            payment.put()
            logging.debug('New transaction added to database')
